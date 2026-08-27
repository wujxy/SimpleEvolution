"""The scientist's deliberation loop, in-world.

The agent owns its loop: it dispatches its own tools (world, assistant,
ledger) and walks itself to one of the two exits — deliver_world or
abstain — both checked against the exit contract at the door. The wall
clock is the harness's allocation, not the scientist's knowledge: the
run may be ended externally at any time (one knock shortly before, then
cut_off on file). Whatever it thinks, decides, and pays
lands in the world's ``.scientist/`` files as it goes; there is nobody
watching in-flight.

Ported from the host-side runtime and the dissolved inworld_cli: the
nudge texts, the terminal validation rules, and the compaction policy
carry over verbatim — one behavioral lineage, a new owner.
"""
from __future__ import annotations

import json
import time

from .native_tools import (
    NATIVE_TERMINAL_ACTIONS,
    NATIVE_TOOLS,
    native_actions,
    wire_assistant_message,
    wire_tool_result,
)
from .research_skills import (
    load_research_skill,
    render_research_skill_catalog,
    render_startup_skills,
)
from .collaboration import ROLE_NAMES

# Consecutive text-only replies tolerated before the idle nudge.
_MAX_IDLE_TURNS = 3
_IDLE_NUDGE = (
    "You sent plain text with no tool call. Acting here means calling a "
    "tool — restate your intent as the appropriate tool call, or conclude "
    "with a terminal tool (deliver_world / abstain)."
)

# --- ported standing texts (scientist.py, verbatim) -------------------------

# The external kill, disclosed once as a knock. The wall clock is the
# harness's allocation, not the scientist's knowledge: no countdown lives
# in its context during the run. One notice shortly before the end, so a
# concluded investigation can still deliver instead of being cut
# mid-flight. (Budget-obligation texts were tried for two arms — one
# honored the wording into a lost handover, the next ignored it — and
# were removed: internal clocks made stopping worse, not better.)
_KILL_KNOCK = (
    "The harness will end this run externally within minutes — the wall "
    "clock it allocated is spent. Whatever is on file survives, and no "
    "conclusion is invented on your behalf. If you judge that a "
    "defensible result is established, deliver it now."
)

def _render_goal_block(goal: str, editable: list[str], base_sha: str,
                       gate_block: str) -> str:
    """The task definition and nothing else: objective, gates, revision,
    writable set. The read-only rest is physical (EROFS mounts), so it is
    stated once, never enumerated."""
    return (
        f"Research objective:\n{goal}\n\n"
        f"Harness Gates:\n{gate_block or '(declared in factual records)'}\n"
        f"\nCurrent accepted revision: {base_sha}\n"
        f"Editable paths (writable world — mounted :rw): "
        f"{json.dumps(editable, ensure_ascii=False)}\n"
        "Read-only world: every other path is mounted :ro — the whole "
        "repo is visible and runnable, but edits outside the editable "
        "set fail at the filesystem."
    )

_COLD_START = (
    "You are beginning this investigation as its principal investigator. "
    "Ground yourself in the live world and determine what uncertainty "
    "deserves attention first; consult past experiments when they are "
    "relevant to that judgment. Your own inspection and small "
    "discriminating probes serve your judgment; substantial "
    "investigations, implementations, and measurement campaigns are work "
    "for Searcher, Proposer, Executor, or Challenger, and you may open "
    "them before any stable judgment exists. Preserve uncertainty when "
    "the evidence is insufficient."
)

# Handover double cap: the prompt teaches ≤400; the door rejects beyond
# 600 — "写超了" is first a violation feeling, and only past the hard cap
# a mechanical rejection. The seat may deliver degraded
# (handover_compliant=false).
_HANDOVER_SOFT_WORD_CAP = 400
_HANDOVER_HARD_WORD_CAP = 600

def build_system_prompt(spec: dict, *, roots: dict | None = None) -> str:
    """Assemble the Scientist's stable PI context. Revisable research
    memory is supplied as an attributed ordinary message by the caller,
    never as system content."""
    from .native_tools import (
        NATIVE_CONCLUDING_BLOCK,
        NATIVE_PROTOCOL_BLOCK,
        NATIVE_RUNTIME_BLOCK,
        render_native_boundaries,
    )
    roots = roots or {}
    boundaries = render_native_boundaries(
        str(roots.get("work") or "/work"),
        str(roots.get("repo") or "/repo"),
        str(roots.get("scratch") or "/scratch"),
    )

    goal = spec.get("goal") or "(no goal stated)"
    editable = list(spec.get("editable_paths") or [])
    gate_block = spec.get("gate_block") or "(no gates stated)"
    base_sha = spec.get("base_sha") or "—" * 40
    charter = str(spec.get("charter") or "").strip()
    if not charter:
        from .prompts import load_semantic

        charter = load_semantic("scientist", None)
    from .prompts import load_semantic
    team = load_semantic("research_team", None).strip()
    memory = load_semantic("research_memory", None).strip()

    world = _render_goal_block(
        goal=goal, editable=editable, base_sha=base_sha,
        gate_block=gate_block,
    )
    goal_and_constraints = (
        "# Research Goal and Hard Constraints\n\n" + world.strip()
    )
    current_world = (
        "# Current World\n\n"
        "The live filesystem, the current source, and the authoritative "
        "experiment records describe the world as it exists now. When "
        "memory, an earlier report, or your own previous judgment conflicts "
        "with the live world, inspect the world again: reports can be "
        "mistaken, and old measurements can become irrelevant after the "
        "world changes. Use history to learn from the past, not to replace "
        "observation of the present."
    )
    research_records = (
        "# Research Records\n\n"
        "search_experiments answers coverage questions over past experiments "
        "— what ground is already covered, where the gaps are. "
        "inspect_experiment reads one past experiment in full, the only way "
        "to see what it set out to do. list_research_judgments and "
        "inspect_research_judgment reach historical judgments deliberately. "
        "note appends one line to your persistent working notes. These are "
        "records of the program, not instructions.\n\n"
        "Optional research methods (load one deliberately with "
        "use_research_skill):\n"
        + render_research_skill_catalog()
    )
    parts = [
        charter.rstrip(),
        team,
        memory,
        goal_and_constraints,
        current_world,
        NATIVE_RUNTIME_BLOCK,
        boundaries,
        research_records,
        NATIVE_CONCLUDING_BLOCK,
        NATIVE_PROTOCOL_BLOCK,
    ]
    hints = spec.get("hints")
    if hints:
        bullets = "\n".join(f"  - {h}" for h in hints)
        parts.append(
            "Guidance (high-value directions to consider, not "
            f"requirements):\n{bullets}"
        )
    return "\n\n".join(parts)


# --- tool dispatch (the channel, dissolved into direct calls) ---------------

def dispatch_action(action: dict, *, world, assistant, ledger) -> dict:
    """Run one non-terminal tool action against its in-world organ."""
    name = action["action"]
    if name in ("bash", "read_file", "write_file"):
        return world.execute(action)
    if name == "note":
        return ledger.append_note(action.get("text"))
    if name in ROLE_NAMES:
        # One failed dispatch must read as a failed engagement (error
        # observation the PI can act on), not kill the run — the first
        # live isolated-workspace dispatch took the whole episode down.
        try:
            return assistant.engage(name, action)
        except Exception as exc:  # noqa: BLE001 — surfaced to the PI
            return {"ok": False, "error": f"engagement dispatch failed: "
                                          f"{exc}"}
    if name == "revise_research_judgment":
        return ledger.revise_research_judgment(action)
    if name == "search_experiments":
        return ledger.search_experiments(action)
    if name == "inspect_experiment":
        return ledger.inspect_experiment(action)
    if name == "inspect_originating_research_state":
        return ledger.inspect_originating_research_state(action)
    if name == "list_research_judgments":
        return ledger.list_research_judgments(action)
    if name == "inspect_research_judgment":
        return ledger.inspect_research_judgment(action)
    if name == "use_research_skill":
        skill_id = str(action.get("skill_id") or "")
        try:
            text = load_research_skill(skill_id)
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skill_id": skill_id, "text": text}
    return {"ok": False, "error": f"unknown tool: {name}"}


# --- the exit contract (checked at the door) --------------------------------

def _validate_handover(action: dict) -> tuple[dict | None, str]:
    handover = action.get("handover")
    if not isinstance(handover, dict):
        return None, "deliver_world.handover must be an object"
    for key in ("dead_ends", "open_questions"):
        value = handover.get(key)
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return None, (
                f"handover.{key} must be a non-empty list of strings"
            )
    warning = handover.get("warning")
    if not isinstance(warning, str) or not warning.strip():
        return None, "handover.warning must be non-empty"
    words = sum(
        len(str(item).split())
        for item in (
            handover["dead_ends"] + handover["open_questions"]
            + [warning],
        )
    )
    if words > _HANDOVER_SOFT_WORD_CAP:
        print(
            f"[agent] handover at {words} words exceeds the taught cap of "
            f"{_HANDOVER_SOFT_WORD_CAP} (hard cap "
            f"{_HANDOVER_HARD_WORD_CAP})",
            flush=True,
        )
    if words > _HANDOVER_HARD_WORD_CAP and action.get(
            "handover_compliant") is not False:
        return None, (
            f"handover exceeds the hard cap of {_HANDOVER_HARD_WORD_CAP} "
            f"words (got {words}): rewrite it as a map, not a memoir — "
            "or, if you have already retried twice, deliver with "
            "\"handover_compliant\": false to deliver degraded"
        )
    out = {
        "handover": {
            "dead_ends": [s.strip() for s in handover["dead_ends"]],
            "open_questions": [
                s.strip() for s in handover["open_questions"]
            ],
            "warning": warning.strip(),
        },
    }
    if action.get("handover_compliant") is False:
        out["handover_compliant"] = False
    return out, ""


def validate_conclusion(action: dict, *, ledger) -> tuple[dict | None, str]:
    """The door check for a terminal action: ``(conclusion, rejection)``.

    Exactly one of the two is non-empty. Deliver and abstain both
    require the research state on file — an exit with nothing on file is
    a protocol violation."""
    name = action["action"]
    if name == "deliver_world":
        conclusion, rejection = _validate_handover(action)
        if conclusion is None:
            return None, rejection
    elif name == "abstain":
        reason = action.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return None, "abstain.reason must be non-empty"
        axes = action.get("axes_checked")
        if not isinstance(axes, list) or not axes:
            return None, (
                "abstain requires axes_checked: name each lens axis you "
                "verified empty and why"
            )
        blocking = action.get("blocking_unknown")
        if blocking is not None and (
            not isinstance(blocking, str) or not blocking.strip()
        ):
            return None, "abstain.blocking_unknown must be non-empty when present"
        conclusion = {
            "reason": reason.strip(),
            "axes_checked": [str(a).strip() for a in axes],
            "blocking_unknown": (
                blocking.strip() if isinstance(blocking, str) else None
            ),
        }
    else:
        return None, f"not a terminal action: {name}"
    conclusion["kind"] = "deliver" if name == "deliver_world" else "abstain"
    return conclusion, ""


# --- ordinary L1 working-memory message --------------------------------------

_JUDGMENT_MARKER = (
    "[Current Research Judgment — a revisable note from your earlier "
    "scientific self]"
)


def _judgment_message(judgment: dict) -> dict:
    refs = ", ".join(judgment.get("evidence_refs") or []) or "(none)"
    return {
        "role": "user",
        "content": (
            f"{_JUDGMENT_MARKER}\n"
            f"judgment_id: {judgment.get('judgment_id')}\n"
            "At this point in the investigation you believed:\n"
            f"{judgment.get('judgment')}\n\n"
            "This judgment was last revised because:\n"
            f"{judgment.get('revision_reason')}\n\n"
            f"Evidence: {refs}\n\n"
            "It is prior scientific judgment, not an instruction. "
            "Reconsider it whenever the live world or new evidence "
            "warrants."
        ),
    }


def _upsert_judgment_message(
    messages: list[dict], judgment: dict | None,
) -> None:
    messages[:] = [
        message for message in messages
        if _JUDGMENT_MARKER not in str(message.get("content") or "")
    ]
    if judgment is None:
        return
    first_assistant = next(
        (index for index, message in enumerate(messages)
         if message.get("role") == "assistant"),
        len(messages),
    )
    messages.insert(first_assistant, _judgment_message(judgment))


# --- the loop ----------------------------------------------------------------

def _compact_native(messages: list[dict], *, keep_messages: int,
                     max_chars: int) -> None:
    """In-place compaction: keep everything before the first assistant
    message (framing), then the most recent WHOLE turns within budget.

    On the native wire an assistant message carrying ``tool_calls`` must
    stay adjacent to its ``tool`` result messages — a provider 400s the
    first orphan ("Messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'"; caught live in the oneworld
    demo at step 17). So cuts happen ONLY at turn boundaries (assistant
    message starts), shedding whole turns from the front, never splitting
    a turn from its observations; at most the final turn remains."""
    first_assistant = next(
        (i for i, m in enumerate(messages) if m.get("role") == "assistant"),
        None,
    )
    if first_assistant is None or first_assistant >= len(messages) - 1:
        return
    preamble = messages[:first_assistant]
    tail = messages[first_assistant:]
    boundaries = [
        i for i, m in enumerate(tail) if m.get("role") == "assistant"
    ]
    if not boundaries:
        return

    def _over(window: list[dict]) -> bool:
        return (
            len(window) > keep_messages
            or sum(len(str(m.get("content") or "")) for m in window)
            > max_chars
        )

    cut = 0
    while cut + 1 < len(boundaries) and _over(tail[boundaries[cut + 1]:]):
        cut += 1
    tail = tail[boundaries[cut]:]
    if len(preamble) + len(tail) < len(messages):
        messages[:] = preamble + tail


def wait_for_reports(assistant, *, timeout_seconds: float) -> dict:
    """Park the loop until the next team engagement exits (or timeout).

    Observation only: this NEVER finalizes a job and NEVER appends to
    the conversation. There is exactly one intake point for collaborator
    reports — the pump between model calls (``assistant.poll`` at loop
    top) — so a user message can never land between a tool_calls
    message and its tool result (the wire invariant demo-2 died
    violating). The wait result just tells the seat mail has arrived;
    the pump delivers it right before the seat's next thought."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        landed = assistant.finished_pending()
        if landed or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    if landed:
        return {"ok": True, "landed": landed,
                "note": "its report arrives as its own message before "
                        "your next thought"}
    return {"ok": True, "timeout": True,
            "note": "no report landed yet — dispatched jobs are still "
                    "running"}


def _collaborator_report_message(result: dict) -> str:
    """Render one attributed team report as an ordinary user message."""
    header = (
        f"[Research collaborator report | role={result.get('role')} | "
        f"collaborator_id={result.get('collaborator_id')}]"
    )
    if not result.get("ok"):
        return f"{header}\nstatus: failed\nerror: {result.get('error') or 'engagement failed'}"
    metrics = json.dumps(result.get("metrics") or {}, ensure_ascii=False)
    return (
        f"{header}\n"
        f"report: {result.get('report_digest') or result.get('self_report_digest')}\n"
        f"artifacts: {result.get('artifacts') or result.get('diff_summary') or '(none)'}\n"
        f"metrics: {metrics}\n"
        f"uncertainty: {result.get('uncertainty') or '(not stated)'}\n"
        "status: collaborator testimony; not Scientist judgment"
    )


def _log(message: str) -> None:
    print(f"[scientist {time.strftime('%H:%M:%S')}] {message}",
          flush=True)


def run_episode(
    *,
    model,
    system_prompt: str,
    messages: list[dict],
    world,
    assistant,
    ledger,
    steps_budget: int,
    wall_seconds: float,
    session=None,
    compact_keep_messages: int = 400,
    compact_max_chars: int = 200_000,
) -> dict:
    """The native tool-calling deliberation loop. Returns the episode
    result dict (outcome, conclusion, steps, usages, actions); the CLI
    writes the conclusion to the world's ``.scientist/conclusion.json``
    after the loop returns — that file is the exit contract."""
    started = time.monotonic()
    deadline = started + wall_seconds
    usages: list = []
    action_log: list[dict] = []
    knocked = False
    idle_turns = 0
    outcome = "cut_off"
    conclusion: dict | None = None
    _upsert_judgment_message(messages, ledger.current_judgment())

    def _emit(message: dict) -> None:
        """Append to the live list AND the wire log — the wire log is the
        single source of truth a resume rebuilds from."""
        messages.append(message)
        if session is not None:
            session.append_wire(message)

    if session is not None and not session.wire_path.exists():
        # cold start: the framing (opening messages + judgment note) is
        # part of the record too
        for message in messages:
            session.append_wire(message)

    def _nudge(text: str) -> None:
        _emit({"role": "user", "content": text})

    def _deliver(report: dict) -> None:
        _emit({"role": "user",
               "content": _collaborator_report_message(report)})

    try:
        for index in range(steps_budget):
            step = index + 1
            # Finished engagements land as messages before the next model call.
            for report in assistant.poll():
                _deliver(report)
                _log(f"{report.get('role')} engagement "
                     f"{report.get('collaborator_id')} finished")
            if not knocked and (
                    deadline - time.monotonic()
                    < min(600.0, 0.05 * wall_seconds)):
                _nudge(_KILL_KNOCK)
                knocked = True
            # Graceful wall exit: with less than ~10% of the wall (cap 90s)
            # left there is no room for another model call plus a conclusion
            # — conclude cut_off on file instead.
            wall_margin = min(90.0, 0.1 * wall_seconds)
            remaining = deadline - time.monotonic()
            if remaining < wall_margin:
                _log(f"step {step}/{steps_budget}: wall nearly spent; "
                     "concluding cut_off")
                break

            _log(f"step {step}/{steps_budget}: thinking")
            reply = model.complete(
                system=system_prompt, messages=messages,
                timeout_seconds=remaining, tools=list(NATIVE_TOOLS),
            )
            if reply.usage is not None:
                usages.append(reply.usage)
                ledger.note_usage(reply.usage)
            actions = native_actions(reply)
            if not actions:
                # Text-only turn: archive and nudge; the model acts by calling.
                idle_turns += 1
                _log(f"step {step}: text-only reply")
                _emit({"role": "assistant", "content": reply.text})
                if idle_turns >= _MAX_IDLE_TURNS:
                    _nudge(_IDLE_NUDGE)
                    idle_turns = 0
                continue
            idle_turns = 0
            _log(f"step {step}: " + ", ".join(a["action"] for a in actions))

            # The terminal contract: exactly one terminal call, alone.
            terminals = [a for a in actions
                         if a["action"] in NATIVE_TERMINAL_ACTIONS]
            if terminals and len(actions) == 1:
                action = actions[0]
                action_log.append({"action": action["action"], "step": step})
                _emit(wire_assistant_message(reply, actions))
                conclusion, rejection = validate_conclusion(
                    action, ledger=ledger)
                if conclusion is not None:
                    outcome = conclusion["kind"]
                    return _result(
                        outcome, conclusion, step, usages, action_log,
                    )
                # Rejected at the door: the rejection is the observation;
                # the research continues.
                outcome = "cut_off"
                conclusion = None
                observation = {"ok": False, "error": rejection}
                _emit(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                continue

            _emit(wire_assistant_message(reply, actions))
            for action in actions:
                name = action["action"]
                if name in NATIVE_TERMINAL_ACTIONS:
                    observation = {
                        "ok": False,
                        "error": "terminal actions are sent ALONE, never "
                                 "alongside other calls; re-send it as your "
                                 "only call",
                    }
                elif "_arguments_raw" in action:
                    observation = {
                        "ok": False,
                        "error": "tool arguments were not valid JSON: "
                                 f"{action['_arguments_raw'][:200]}",
                    }
                elif name == "wait":
                    requested = action.get("timeout_seconds")
                    try:
                        seconds = min(float(requested or 600.0), 3600.0)
                    except (TypeError, ValueError):
                        seconds = 600.0
                    wall_left = deadline - time.monotonic()
                    observation = wait_for_reports(
                        assistant,
                        timeout_seconds=max(
                            1.0, min(seconds, wall_left - 5.0)),
                    )
                else:
                    observation = dispatch_action(
                        action, world=world, assistant=assistant,
                        ledger=ledger,
                    )
                action_log.append({"action": name, "step": step})
                _emit(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                if name == "revise_research_judgment" and observation.get("ok"):
                    _upsert_judgment_message(messages, ledger.current_judgment())
            _compact_native(
                messages, keep_messages=compact_keep_messages,
                max_chars=compact_max_chars,
            )
            _upsert_judgment_message(messages, ledger.current_judgment())

        # Budget expiry: conclude cut_off — what is on file survives.
        conclusion = {"kind": "cut_off", "reason": "budget exhausted"}
        return _result("cut_off", conclusion, steps_budget, usages,
                       action_log)
    finally:
        abandoned = assistant.shutdown()
        if abandoned:
            _log(f"{abandoned} collaborator engagement(s) abandoned at exit")


def _result(outcome, conclusion, steps, usages, action_log) -> dict:
    return {
        "outcome": outcome,
        "conclusion": conclusion,
        "steps": steps,
        "usages": usages,
        "actions": action_log,
    }
