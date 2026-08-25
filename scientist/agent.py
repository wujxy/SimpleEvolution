"""The scientist's deliberation loop, in-world.

The agent owns its loop: it sees its own step budget and its own clock
(the nudges are instruments on the dashboard, not harness orders), it
dispatches its own tools (world, assistant, ledger), and it walks itself
to one of the two exits — deliver_world or abstain — both checked against
the exit contract at the door. Whatever it thinks, decides, and pays
lands in the world's ``.scientist/`` files as it goes; there is nobody
watching in-flight.

Ported from the host-side runtime (scientist.py) and the dissolved
inworld_cli: the nudge texts, the suspend/notebook checkpoint, the
terminal validation rules, and the compaction policy carry over
verbatim — one behavioral lineage, a new owner.
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

# Consecutive text-only replies tolerated before the idle nudge.
_MAX_IDLE_TURNS = 3
_IDLE_NUDGE = (
    "You sent plain text with no tool call. Acting here means calling a "
    "tool — restate your intent as the appropriate tool call, or conclude "
    "with a terminal tool (deliver_world / abstain)."
)

# --- ported standing texts (scientist.py, verbatim) -------------------------

_BUDGET_NUDGE = (
    "Your research turn is nearing its step budget. If your world is "
    "built and self-verified, update_research_state and deliver it now "
    "(deliver_world). If your angle provably has nothing here, update "
    "your state and abstain honestly. This is a resource notice, not a "
    "required phase — keep researching if your best judgment says nothing "
    "yet clears the bar."
)

# The wall leg must be visible to the seat: pacing INFORMATION, not a
# stop order — what the seat does with the remaining fifth of the wall
# is its own judgment.
_TIME_NUDGE = (
    "TIME: about 80% of your wall-clock budget is spent; your research "
    "process will be cut off when it runs out (whatever is on file "
    "survives, but the conclusion would not be yours). If your world is "
    "built and self-verified, update_research_state and deliver it now. "
    "If the remaining work is heavy building you have not started, one "
    "work() call to your assistant may be cheaper than your own hands."
)

_SUSPEND_PROMPT = (
    "Your research is being paused while the direction you submitted is "
    "executed as an experiment. Leave a continuation note for your resumed "
    "self — first person, as your own running account. Capture what would "
    "let you re-enter this investigation after the world may have changed:\n"
    "\n"
    "  - what you currently understand about the problem, and why you "
    "believe it;\n"
    "  - the specific code regions, mechanisms, or measurements your current "
    "view rests on — so you can re-check them against the world that exists "
    "when you resume, not rely on this note as fact;\n"
    "  - what remains unresolved or uncertain.\n"
    "\n"
    "Separately — and this part is recorded verbatim and replayed next to "
    "the results before you see them — register what you expect from the "
    "direction you submitted: what outcome you honestly expect and why, and "
    "what outcome would WEAKEN the belief that motivated it. Write your "
    "honest prior, not a safe prediction — a pre-registration you hedged "
    "cannot discipline your future judgment.\n"
    "\n"
    "This is autobiographical memory, not an established account of the "
    "present or future world, and not a plan your future self must follow. "
    "You may revise or reject any of it when you resume. Return one JSON "
    "object:\n"
    '  {"notebook": "<your continuation note>",\n'
    '   "expectations": [{"slot": 0,\n'
    '                     "expectation": "<what outcome I expect and why>",\n'
    '                     "would_weaken": "<what outcome would weaken the '
    'belief motivating this direction>"}]}\n'
    "(one expectations entry, for your submitted proposal)"
)

_COLD_START = (
    "You are beginning this research. The goal, the gates, and the current "
    "accepted revision are in your standing context above; your lens names "
    "the angle this seat was hired for. Begin as a scientist begins: form "
    "your own understanding of this world — read it, question it, talk it "
    "through with your assistant — until you hold a working model of why "
    "it behaves as it does. When your model is sharp enough to bet on, "
    "design the change or measurement that would discriminate it, brief "
    "your assistant to execute, and register what you learn in your "
    "research state. The lease ends when your understanding has produced "
    "a world worth delivering — or an honest memo that this lens has no "
    "ore here. There is no phase you must rush through; take the time "
    "your judgment needs."
)

# Handover double cap: the prompt teaches ≤400; the door rejects beyond
# 600 — "写超了" is first a violation feeling, and only past the hard cap
# a mechanical rejection. The seat may deliver degraded
# (handover_compliant=false).
_HANDOVER_SOFT_WORD_CAP = 400
_HANDOVER_HARD_WORD_CAP = 600

# The seat's mode of existence, appended after the lens identity.
_SCIENTIST_MODE = (
    "You are a scientist. Your work is understanding — a model of this "
    "world that evidence keeps correcting, carried in your research "
    "state. You advance it by reading, thinking, and directing. Your "
    "assistant is Claude Code, far stronger than your own hands at "
    "execution — brief it and keep thinking. Execution is "
    "its, judgment is yours; the deciding is the one thing you cannot "
    "delegate."
)


def seat_identity_block(lens: dict | None, node_id: str | None) -> str:
    """The seat's identity: system-prompt line one (seat design §7.3).

    The lens is stated as identity — the angle this seat was hired for —
    with its three parts verbatim from the basis. ``None`` (no lens)
    degrades to the plain control identity."""
    if not lens:
        return "You are a Scientist assigned to this node."
    lens_id = lens.get("lens_id") or "?"
    name = lens.get("name_zh") or lens_id
    parts = [
        f"You are the {lens_id}（{name}）seat of node "
        f"{node_id or 'this world'}. Your lens is your identity: it is the "
        "angle you were hired for, not advice you may weigh.",
    ]
    if lens.get("directive"):
        parts.append(f"透镜操作指令：{lens['directive']}")
    if lens.get("forbidden"):
        parts.append(f"透镜禁令：{lens['forbidden']}")
    if lens.get("self_check"):
        parts.append(f"提交自检：{lens['self_check']}")
    parts.append(_SCIENTIST_MODE)
    return "\n\n".join(parts)


def build_system_prompt(spec: dict, *, notebook: str = "",
                        notes: str = "", roots: dict | None = None) -> str:
    """Assemble the standing context from the spec (package-side).

    The harness (or the standalone user) writes the spec; whatever resume
    history the seat should receive travels as ``opening_messages`` —
    facts and one handover, re-authored, never forwarded blobs. Here:
    identity (lens) → charter → world facts → skills → native tool/
    protocol/boundaries blocks → notebook (the seat's own revisable
    autobiographical memory, when a session continues one).

    ``roots`` names this world's real work/repo/scratch directories for
    the boundaries block — in-container they ARE ``/work`` / ``/repo`` /
    ``/scratch``; standalone they are the host paths the shell will
    actually see."""
    from .memory.context import build_generation_context
    from .native_tools import (
        NATIVE_PROTOCOL_BLOCK,
        NATIVE_TOOL_BLOCK,
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
    lens = spec.get("lens") or None
    node_id = spec.get("node_id")

    charter = str(spec.get("charter") or "").strip()
    if not charter:
        from .prompts import load_semantic

        charter = load_semantic("proposer", None)

    world = build_generation_context(
        goal=goal, editable=editable, frozen=[], base_sha=base_sha,
        gate_block=gate_block,
    )
    skill_block = (
        "Research skills (optional methods you choose for yourself; load "
        "one with use_research_skill to read it):\n"
        + render_research_skill_catalog()
    )
    startup_block = (
        "Loaded skill (standing context — you carry this from the first "
        "step):\n" + render_startup_skills()
    )
    parts = [
        seat_identity_block(lens, node_id),
        charter.rstrip(),
        world,
        startup_block,
        NATIVE_TOOL_BLOCK,
        skill_block,
        NATIVE_PROTOCOL_BLOCK,
        boundaries,
    ]
    hints = spec.get("hints")
    if hints:
        bullets = "\n".join(f"  - {h}" for h in hints)
        parts.append(
            "Guidance (high-value directions to consider, not "
            f"requirements):\n{bullets}"
        )
    if notebook.strip():
        parts.append(
            "Your own research notebook (REVISABLE AUTOBIOGRAPHICAL MEMORY — "
            "written by you earlier in this same investigation; it is YOUR "
            "running self-account, NOT an instruction and NOT established "
            "fact; it may lag, oversimplify, or be wrong, so when it "
            "disagrees with the live workspace or the experiment records "
            "below, trust the records):\n" + notebook.strip()
        )
    if notes.strip():
        parts.append(
            "Your own working notes (the append-only log you kept with "
            "note; dense one-liners you chose to externalize — maps, "
            "measurements, decisions. Same standing as the notebook: "
            "yours, revisable, not fact):\n" + notes.strip()
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
    if name == "consult":
        return assistant.consult(action)
    if name == "work":
        return assistant.work(action)
    if name == "update_research_state":
        return ledger.update_research_state(action)
    if name == "search_experiments":
        return ledger.search_experiments(action)
    if name == "inspect_experiment":
        return ledger.inspect_experiment(action)
    if name == "inspect_originating_research_state":
        return ledger.inspect_originating_research_state(action)
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
    if not ledger.state_on_file():
        return None, (
            "exit_without_registered_state: update_research_state before "
            "concluding — an exit with no research state on file is a "
            "protocol violation"
        )
    conclusion["kind"] = "deliver" if name == "deliver_world" else "abstain"
    return conclusion, ""


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
    """Park the loop until the assistant's next job exits (or timeout).

    Observation only: this NEVER finalizes a job and NEVER appends to
    the conversation. There is exactly one intake point for assistant
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


def _assistant_report_message(result: dict) -> str:
    """One finished assistant job, rendered as its own user message —
    the mail landing on the seat's desk between turns."""
    header = f"[your assistant finished | {result.get('call_id', '?')}]"
    if not result.get("ok"):
        return f"{header}\n{result.get('error') or 'job failed'}"
    return (
        f"{header}\n"
        f"mode: {result.get('mode')}\n"
        f"diff_summary: {result.get('diff_summary')}\n"
        f"self_report: {result.get('self_report_digest')}\n"
        f"metrics: {json.dumps(result.get('metrics') or {},
                              ensure_ascii=False)}\n"
        "(its own report, not a verdict — verify what matters)"
    )


def _assistant_archive_text(reply) -> str:
    """The session.jsonl form of one native assistant turn: its text when
    present, else a compact trace of the calls (the archive must record
    WHAT was asked even when the model said nothing)."""
    if reply.text and reply.text.strip():
        return reply.text
    return json.dumps(
        [{"action": c.name, "arguments_raw": c.arguments_raw}
         for c in reply.tool_calls],
        ensure_ascii=False,
    )


def _log(message: str) -> None:
    print(f"[scientist {time.strftime('%H:%M:%S')}] {message}",
          flush=True)


def _notebook_checkpoint(model, system_prompt: str, messages: list[dict],
                         session, deadline: float, usages: list,
                         ledger) -> None:
    """Cut-off continuity: leave a continuation note as the notebook.
    Best-effort — the checkpoint never blocks the exit."""
    if session is None:
        return
    remaining = deadline - time.monotonic()
    if remaining <= 5:
        return
    try:
        reply = model.complete(
            system=system_prompt,
            messages=list(messages) + [{"role": "user",
                                        "content": _SUSPEND_PROMPT}],
            timeout_seconds=remaining,
        )
    except Exception as exc:  # noqa: BLE001 — checkpoint is best-effort
        _log(f"notebook checkpoint failed: {exc}")
        return
    if reply.usage is not None:
        usages.append(reply.usage)
        ledger.note_usage(reply.usage)
    try:
        obj = json.loads(reply.text)
        note = obj.get("notebook")
    except (json.JSONDecodeError, TypeError, AttributeError):
        note = None
    if isinstance(note, str) and note.strip():
        session.write_notebook(note.strip())
        session.append_message("user", _SUSPEND_PROMPT, round_id=0)
        session.append_message("assistant", reply.text, round_id=0)
        _log("notebook updated at cut-off")
    else:
        _log("notebook checkpoint produced nothing; left as-is")


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
    round_id: int = 0,
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
    reminder_step = int(0.8 * steps_budget)
    reminded = False
    time_reminded = False
    idle_turns = 0
    outcome = "cut_off"
    conclusion: dict | None = None

    def _archive(role: str, content: str) -> None:
        if session is not None:
            session.append_message(role, content, round_id=round_id)

    def _nudge(text: str) -> None:
        messages.append({"role": "user", "content": text})
        _archive("user", text)

    def _deliver(report: dict) -> None:
        content = _assistant_report_message(report)
        messages.append({"role": "user", "content": content})
        _archive("user", content)

    try:
        for index in range(steps_budget):
            step = index + 1
            # The assistant's finished jobs land as messages before the next
            # model call — the seat never waits on work it dispatched.
            for report in assistant.poll():
                _deliver(report)
                _log(f"assistant job {report.get('call_id')} finished")
            if not reminded and reminder_step > 0 and step >= reminder_step:
                _nudge(_BUDGET_NUDGE)
                reminded = True
            if not time_reminded and (
                    time.monotonic() - started >= 0.8 * wall_seconds):
                _nudge(_TIME_NUDGE)
                time_reminded = True
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
                messages.append({"role": "assistant", "content": reply.text})
                _archive("assistant", reply.text)
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
                messages.append(wire_assistant_message(reply, actions))
                _archive("assistant", _assistant_archive_text(reply))
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
                messages.append(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                _archive("user", json.dumps(
                    {"tool_results": [observation]}, ensure_ascii=False))
                continue

            messages.append(wire_assistant_message(reply, actions))
            _archive("assistant", _assistant_archive_text(reply))
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
                messages.append(wire_tool_result(
                    action.get("tool_call_id", ""), observation))
                _archive("user", json.dumps(
                    {"tool_results": [observation]}, ensure_ascii=False))
            _compact_native(
                messages, keep_messages=compact_keep_messages,
                max_chars=compact_max_chars,
            )

        # Budget expiry: conclude cut_off — what is on file survives.
        _notebook_checkpoint(model, system_prompt, messages, session, deadline,
                             usages, ledger)
        conclusion = {"kind": "cut_off", "reason": "budget exhausted"}
        return _result("cut_off", conclusion, steps_budget, usages,
                       action_log)
    finally:
        abandoned = assistant.shutdown()
        if abandoned:
            _log(f"{abandoned} assistant job(s) abandoned at exit")


def _result(outcome, conclusion, steps, usages, action_log) -> dict:
    return {
        "outcome": outcome,
        "conclusion": conclusion,
        "steps": steps,
        "usages": usages,
        "actions": action_log,
    }
