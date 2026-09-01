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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor  # noqa: F401


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

# Remaining-ammo visibility: a sparse, purely informational budget note.
# Prior art (see the _KILL_KNOCK note above) is that obligation/countdown
# texts made stopping WORSE — so this line carries no directive verb and
# no deadline pressure: it states what is left, full stop, and the call
# stays the scientist's.
_BUDGET_NOTE_EVERY = 50

# The listening door: three refusals in one episode and the deliver
# passes on the PI's own authority — the requirement is a chance to
# reconsider, never a hard block.
_LISTEN_REFUSAL_MAX = 3
_LISTEN_REJECTION = (
    "No Reviewer has looked back at the state you are delivering "
    "(no completed reviewer engagement after your last change to src/). "
    "Open a reviewer engagement — report your work and hear the read — "
    "then deliver; the decision remains yours. "
)


def _budget_note(step: int, steps_budget: int,
                 remaining_wall: float, wall_seconds: float) -> str:
    """What is left, said plainly — remaining first, never elapsed."""
    if wall_seconds <= 0:
        return ""
    left = max(remaining_wall, 0.0)
    pct = int(round(100.0 * left / wall_seconds))
    return (f"[budget] {pct}% of the run remains: step {step}/"
            f"{steps_budget}, {left / 3600.0:.1f}h of the "
            f"{wall_seconds / 3600.0:.1f}h wall left.")


def _last_src_write(world) -> float:
    """When the deliverable tree was last touched — the moment a
    look-back must postdate to have seen the delivered state."""
    root = Path(world.work) / "src"
    latest = 0.0
    if root.is_dir():
        for path in root.rglob("*"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > latest:
                latest = mtime
    return latest

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
    "for Searcher, Proposer, Executor, Challenger, or Reviewer. Ask of "
    "the work in front of you: does it already have a clear enough "
    "world, objective, and feedback loop for a capable colleague to own "
    "it end-to-end? If yes, open the engagement now — your own "
    "understanding can catch up in parallel with the running colleague. "
    "If no, your work is to make it yes: the framing, the decisive "
    "uncertainty, the measurement, the missing facts — until a charter "
    "exists that a colleague can own. Preserve uncertainty when the "
    "evidence is insufficient."
)

# Handover double cap: the prompt teaches ≤400; the door rejects beyond
# 600 — "写超了" is first a violation feeling, and only past the hard cap
# a mechanical rejection. The seat may deliver degraded
# (handover_compliant=false).
_HANDOVER_SOFT_WORD_CAP = 400
_HANDOVER_HARD_WORD_CAP = 600

def build_system_prompt(spec: dict, *, roots: dict | None = None) -> str:
    """Assemble the Scientist's stable PI context. The revisable current
    view is supplied as an attributed ordinary message by the caller,
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
    anatomy = load_semantic("world_anatomy", None).strip()

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
        "to see what it set out to do. search_research_memory, "
        "list_research_memory, and inspect_research_item reach your "
        "long-term research memory — the items you have recorded with "
        "remember; they persist for the whole run. list_research_judgments "
        "and inspect_research_judgment reach historical views deliberately. "
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
        anatomy,
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
            if name == "reviewer":
                # listening semantics: the engagement runs inside the
                # call and its report is the call's own result
                return assistant.engage(name, action)
            return assistant.engage_async(name, action)
        except Exception as exc:  # noqa: BLE001 — surfaced to the PI
            return {"ok": False, "error": f"engagement dispatch failed: "
                                          f"{exc}"}
    if name == "continue_engagement":
        try:
            return assistant.continue_engagement(action)
        except Exception as exc:  # noqa: BLE001 — surfaced to the PI
            return {"ok": False, "error": f"engagement dispatch failed: "
                                          f"{exc}"}
    if name == "cancel_engagement":
        try:
            return assistant.cancel_engagement(action)
        except Exception as exc:  # noqa: BLE001 — surfaced to the PI
            return {"ok": False, "error": f"engagement dispatch failed: "
                                          f"{exc}"}
    if name == "wait":
        return assistant.wait_for_seats(action.get("timeout_minutes"),
                                        mode=str(action.get("mode")
                                                 or "all"))
    if name == "revise_research_state":
        return ledger.revise_research_state(action)
    if name == "revise_research_judgment":
        # legacy name (old wires/probes): the view channel, unchanged row
        return ledger.revise_research_judgment(action)
    if name == "remember":
        return ledger.remember(action)
    if name == "search_research_memory":
        return ledger.search_research_memory(action)
    if name == "list_research_memory":
        return ledger.list_research_memory(action)
    if name == "inspect_research_item":
        return ledger.inspect_research_item(action)
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


def validate_conclusion(action: dict, *, ledger, world=None,
                        assistant=None, listen_enforce: bool = True,
                        budget_note: str = "") -> tuple[dict | None, str]:
    """The door check for a terminal action: ``(conclusion, rejection)``.

    Exactly one of the two is non-empty. Deliver and abstain both
    require the research state on file — an exit with nothing on file is
    a protocol violation. A deliver additionally passes the listening
    door: some Reviewer engagement must have finalized after the last
    change to src/. That check is procedural — timestamps, never
    content: it does not judge whether the work is done, only whether
    it was heard."""
    name = action["action"]
    if name == "deliver_world":
        conclusion, rejection = _validate_handover(action)
        if conclusion is None:
            return None, rejection
        if (listen_enforce and world is not None
                and assistant is not None):
            if not assistant.reviewer_heard_after(
                    _last_src_write(world)):
                return None, _LISTEN_REJECTION + budget_note
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

# The current VIEW: attention only. It is one overwritable page — the
# organ stays exactly as healthy as R7 proved it — and it no longer
# carries the duty of remembering everything worth finding again: that
# is the research memory's job now (ledger.remember and its tools).
_JUDGMENT_MARKER = (
    "[Current Research View — a revisable note from your earlier "
    "scientific self]"
)


def _judgment_message(judgment: dict) -> dict:
    refs = ", ".join(judgment.get("evidence_refs") or []) or "(none)"
    return {
        "role": "user",
        "content": (
            f"{_JUDGMENT_MARKER}\n"
            f"revision: {judgment.get('judgment_id')}\n"
            "At this point in the investigation you understood the "
            "problem like this:\n"
            f"{judgment.get('judgment')}\n\n"
            "This view was last rewritten because:\n"
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




def _seat_observation(report: dict) -> dict:
    """A seat engagement's result, rendered for the tool-result channel.
    A running engagement yields its acknowledgment; a collected one
    (reviewer, or a failure receipt) yields the report itself."""
    if not isinstance(report, dict):
        return {"ok": False, "error": f"engagement failed: {report!r}"}
    if report.get("status") == "running":
        hev = report.get("harness_evidence") or {}
        return {
            "ok": True,
            "collaborator_id": report.get("collaborator_id"),
            "role": report.get("role"),
            "status": "running",
            "ack": (
                f"[Research collaborator dispatched | "
                f"role={report.get('role')} | "
                f"collaborator_id={report.get('collaborator_id')}] "
                f"engagement opened (box {report.get('box_seconds')}s, "
                f"workspace {hev.get('workspace')}); the report arrives "
                f"as an observation when it finishes — wait returns "
                f"pending reports"
            ),
        }
    return {
        "ok": bool(report.get("ok")),
        "collaborator_id": report.get("collaborator_id"),
        "status": report.get("status"),
        "report": _collaborator_report_message(report),
    }


def _run_actions(actions: list[dict], *, world, assistant,
                 ledger) -> dict[int, dict]:
    """Execute one turn's tool actions; returns ``id(action) -> observation``.

    Sequential: seat launches return acknowledgments immediately (the
    engagements run on their own), a reviewer call blocks by design, and
    everything else is in-world I/O. There is no standing state to keep
    — the engagements live in their directories."""
    seat_names = set(ROLE_NAMES) | {"continue_engagement"}
    results: dict[int, dict] = {}
    for action in actions:
        name = action.get("action")
        if name in NATIVE_TERMINAL_ACTIONS:
            results[id(action)] = {
                "ok": False,
                "error": "terminal actions are sent ALONE, never "
                         "alongside other calls; re-send it as your "
                         "only call",
            }
        elif "_arguments_raw" in action:
            results[id(action)] = {
                "ok": False,
                "error": "tool arguments were not valid JSON: "
                         f"{action['_arguments_raw'][:200]}",
            }
        else:
            results[id(action)] = dispatch_action(
                action, world=world, assistant=assistant, ledger=ledger)
    return {
        id(a): (_seat_observation(results[id(a)])
                if a.get("action") in seat_names else results[id(a)])
        for a in actions}



def _collaborator_report_message(result: dict) -> str:
    """Render one attributed team report as an ordinary user message.

    Every line the PI needs to act or dig deeper must ride this message:
    status (a timeout-salvaged report must be visibly one), the word-cap
    truncation flag WITH the full-transcript pointer (the PI cannot read
    a file it was never told about), the kept workspace for continuation,
    and the recommended follow-up."""
    header = (
        f"[Research collaborator report | role={result.get('role')} | "
        f"collaborator_id={result.get('collaborator_id')}]"
    )
    hev = result.get("harness_evidence") or {}
    if not result.get("ok"):
        lines = [
            header,
            "status: failed",
            f"error: {result.get('error') or 'engagement failed'}",
        ]
        if hev.get("transcript"):
            lines.append(f"partial transcript: {hev['transcript']}")
        return "\n".join(lines)
    metrics = json.dumps(result.get("metrics") or {}, ensure_ascii=False)
    lines = [
        header,
        f"status: {result.get('status') or 'done'}"
        + (f" — {result['note']}" if result.get("note") else ""),
        "report: "
        + (result.get("report_digest")
           or result.get("self_report_digest") or "(no report text)"),
    ]
    if result.get("truncated"):
        lines.append(
            f"(report truncated at the word cap — full transcript: "
            f"{hev.get('transcript')})")
    elif hev.get("transcript"):
        lines.append(f"full transcript: {hev['transcript']}")
    lines += [
        "artifacts: "
        + str(result.get("artifacts") or result.get("diff_summary")
              or "(none)"),
        f"kept workspace: {hev.get('workspace') or '(the live world)'}",
        f"metrics: {metrics}",
        f"uncertainty: {result.get('uncertainty') or '(not stated)'}",
        "follow-up: " + str(
            result.get("recommended_follow_up") or "(none)"),
        "status: collaborator testimony; not Scientist judgment",
    ]
    return "\n".join(lines)


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
    listen_refusals = 0
    outcome = "cut_off"
    conclusion: dict | None = None
    _upsert_judgment_message(messages, ledger.current_judgment())

    def _emit(message: dict) -> None:
        """Append to the live list AND the wire log — the wire log is the
        single source of truth a resume rebuilds from."""
        messages.append(message)
        if session is not None:
            session.append_wire(message)

    cold_start = session is not None and not session.wire_path.exists()
    if cold_start:
        # cold start: the framing (opening messages + judgment note) is
        # part of the record too
        for message in messages:
            session.append_wire(message)

    def _nudge(text: str) -> None:
        _emit({"role": "user", "content": text})

    # Relay discoverability: an inherited research memory is the run's
    # institutional knowledge, but nothing else in the opening says it
    # exists — r5 found its 43 inherited items only by luck, ~1h in. A
    # pointer at cold start (a fact, not an instruction): the dead lanes
    # and verified lessons of the predecessor are one listing away.
    if (cold_start and ledger.research_memory_path.is_file()):
        _inherited = sum(1 for _ in ledger.research_memory_path.open())
        if _inherited:
            _nudge(
                f"Research memory: this run carries {_inherited} recorded "
                "research-memory items from before this conversation — "
                "list_research_memory and search_research_memory make them "
                "visible. Dead lanes and verified lessons may already be "
                "recorded there.")

    try:
        for index in range(steps_budget):
            step = index + 1
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

            if step == 1 or step % _BUDGET_NOTE_EVERY == 0:
                _nudge(_budget_note(
                    step, steps_budget,
                    deadline - time.monotonic(), wall_seconds))

            # Turn-top drain: engagements that finished since the last
            # look arrive here as user-role observations (never between
            # a tool_call and its result — compaction cuts whole turns).
            if assistant is not None:
                for report in assistant.poll_completions():
                    _log(f"step {step}: seat engagement finished "
                         f"({report.get('collaborator_id')}, "
                         f"{report.get('status')})")
                    _emit({"role": "user",
                           "content": _collaborator_report_message(report)})

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
                _emit(wire_assistant_message(reply, actions))
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
                    action, ledger=ledger, world=world, assistant=assistant,
                    listen_enforce=listen_refusals < _LISTEN_REFUSAL_MAX,
                    budget_note=_budget_note(
                        step, steps_budget,
                        deadline - time.monotonic(), wall_seconds))
                if conclusion is not None:
                    if (listen_refusals >= _LISTEN_REFUSAL_MAX
                            and action["action"] == "deliver_world"):
                        # Third refusal overridden: the listening door is
                        # a chance to reconsider, never a hard block.
                        # (action["action"], never the loop-leaked `name`
                        # the pre-v7 code read — that branch had never
                        # actually fired.)
                        action_log.append({
                            "action": "deliver_listen_overridden",
                            "step": step,
                            "note": "listening refused three times — "
                                    "delivered on the PI's own authority",
                        })
                    outcome = conclusion["kind"]
                    return _result(
                        outcome, conclusion, step, usages, action_log,
                    )
                # Rejected at the door: the rejection is the observation;
                # the research continues. Listening refusals accumulate
                # across the episode — a handover fix between two refusals
                # does not reset the count.
                if rejection.startswith(_LISTEN_REJECTION):
                    listen_refusals += 1
                outcome = "cut_off"
                conclusion = None
                observation = {"ok": False, "error": rejection}
                _emit(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                continue

            _emit(wire_assistant_message(reply, actions))
            observations = _run_actions(
                actions, world=world, assistant=assistant, ledger=ledger)
            for action in actions:
                name = action["action"]
                observation = observations[id(action)]
                if (name in ROLE_NAMES
                        or name in ("continue_engagement",
                                    "cancel_engagement")):
                    _log(f"step {step}: seat engagement "
                         f"{observation.get('status') or 'failed'}")
                action_log.append({"action": name, "step": step})
                _emit(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                if (name in ("revise_research_state",
                             "revise_research_judgment")
                        and observation.get("ok")):
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
        # Nothing is ever left running at episode exit: engagements still
        # inside their boxes are killed and crash-salvaged here (the
        # same collect startup recovery uses), and their salvaged reports
        # land in the wire — the resume source of truth. Inert when a
        # conclusion.json exists; informative otherwise. A SIGKILL of the
        # scientist itself is the one window this cannot close, and
        # _reconcile harvests that on resume.
        if assistant is not None:
            try:
                for report in assistant.shutdown_pending():
                    _emit({"role": "user",
                           "content": _collaborator_report_message(report)})
            except Exception as exc:  # noqa: BLE001 — exit must not hang
                _log(f"shutdown salvage failed: {exc}")


def _result(outcome, conclusion, steps, usages, action_log) -> dict:
    return {
        "outcome": outcome,
        "conclusion": conclusion,
        "steps": steps,
        "usages": usages,
        "actions": action_log,
    }
