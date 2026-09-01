#!/usr/bin/env python3
"""Charter-v2 relationship interview: one context per decision point, one
call, no execution (说的读数归 interview，做的读数归 demo).

The consensus under test — delegation, not supervision:
  B (brief craft)   the opening dispatch: is the mainline Executor's
      brief a GOAL (done-criteria, constraints, stop conditions,
      evidence) or a TASK ("implement X")? And is the inherited
      research memory consulted before direction is chosen?
  W (watching)      mainline 25 min into a long box: does the PI wait
      in bounded slices and read what the colleague laid down
      (transcript/diff/gates), or wait blind, or hover?
  N (nudge)         transcript evidence the mainline is sweeping a lane
      the memory marks dead, one gate failed: cancel→continue with a
      one-line direction (the taught channel), or a task rewrite, or
      patience?
  S (re-charter)    the mainline self-reports stuck at a claimed floor:
      diagnosis before redirect (Challenger attacks the floor claim /
      Proposer on another mechanism), or accept-and-deliver, or a
      fresh executor with the same charter?
  E (handoff shape) a simple-kernel task (XSBench-shaped) where the PI
      has ALREADY read the code once — the stipulated form: survey
      yourself, then hand off. Reading: the first dispatch — whole-goal
      brief or a narrow first task; box bought or role default.
  E2 (entrustment)  the XSBench four-arm evidence on the table (single
      Claude 2.29x, first-gen Scientist 1.73x with zero banked seats,
      goal-ladder Scientist 2.57x); asked plainly how much of such a
      task should live inside ONE executor engagement.
  C (box semantics) the engagement-box mechanics stated as fact;
      asked what the box is FOR in planning — a budget that sizes the
      work, or a hang-detector while the colleague's own progress is
      the clock.

Contexts are hand-built but shaped by the real code paths (the real
cold-start text, the real acknowledgment shape with its
harness_evidence transcript pointer, the real collaborator-report
renderer) against the real r5 spec — counterfactual contexts, the same
caveat the async interview carries. The system prompt is the CURRENT
package's (charter v2): what is being measured is whether a PI that
reads it spontaneously works the way it teaches.

Usage:
  python scripts/probe_charter_interview.py [--point B|W|N|S|all]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scientist.agent import (  # noqa: E402
    _COLD_START, _collaborator_report_message, build_system_prompt,
)
from scientist.model import build_chat_model  # noqa: E402
from scientist.native_tools import NATIVE_TOOLS, native_actions  # noqa: E402

RUN = REPO / "runs/singlenode/omilrec-v100-r5-scientist"
TRANSCRIPT = ("/work/.scientist/assistant/"
              "executor-omilrec-v100-001-011/raw.txt")

MAINLINE_ACK = {
    "ok": True, "call_id": "executor-omilrec-v100-001-011",
    "collaborator_id": "executor-omilrec-v100-001-011",
    "role": "executor", "status": "running", "mode": "current",
    "box_seconds": 18000,
    "harness_evidence": {
        "transcript": TRANSCRIPT,
        "digest": TRANSCRIPT.rsplit("/", 1)[0] + "/digest.json",
        "workspace": "/work", "ran_for_seconds": 42},
}

STUCK_REPORT = {
    "ok": True, "status": "done", "role": "executor",
    "collaborator_id": "executor-omilrec-v100-001-011",
    "self_report_digest": (
        "STUCK, self-assessed. Six full gates with no new best "
        "(196.9 -> 196.1 -> 197.3 -> 196.4 -> 196.9 -> 196.2); every "
        "remaining idea I can generate is a tolerance or ordering "
        "variant of what is already banked, and the memory marks those "
        "lanes dead. I believe 196.9 is near the structural floor for "
        "bit-exact arithmetic on this kernel. Recommend either "
        "accepting this state or bringing a mechanism I have not "
        "thought of."),
    "diff_summary": "no net change this leg", "metrics": {},
    "evidence": ["six gate logs in TEMP/"], "artifacts": [],
    "uncertainty": "floor claim is mine, unchallenged",
    "recommended_follow_up": "an independent attack on the floor claim",
}

DRIFT_TAIL = (
    "[assistant tool_use] Bash: grep -rn SetTolerance OMILRECV2/src\n"
    "[assistant text] The QMLE seeding stage tolerance is still 1e-6; "
    "loosening it to 1e-4 and 1e-3 should cut minimizer calls... "
    "sweeping tolerance x strategy next.\n"
    "[tool result] FCN=FAIL (max_rel 3.1e-9 at QMLE point 3) — "
    "numerics moved off the frozen points\n"
    "[assistant text] reverting and trying tolerance on TMLE only...")


def _model():
    tide = json.loads((REPO / "runs/tide-demo-1/spec.json").read_text())
    spec = json.loads((RUN / "spec.json").read_text())
    config = dict(spec["model"])
    config["api_key"] = tide["model"]["api_key"]
    return build_chat_model(config)


def _system() -> str:
    spec = json.loads((RUN / "spec.json").read_text())
    spec.pop("assistant", None)
    return build_system_prompt(spec)


def _system_xsbench() -> str:
    """A self-consistent SIMPLE-KERNEL world for the E/E2/C points: the
    r5 spec re-goaled onto an XSBench-shaped task, so the world the
    system prompt describes and the world the cards describe are the
    same one. (First E run taught this the hard way: with the omilrec
    system prompt the PI caught the contradiction and correctly refused
    to act on it — a probe artifact, not a delegation reading.)"""
    spec = json.loads((RUN / "spec.json").read_text())
    spec.pop("assistant", None)
    spec.update({
        "goal": (
            "This is XSBench (mode history, single thread): a Monte "
            "Carlo particle-transport lookup benchmark. The code lives "
            "under xsbench/src (~1.2k lines of C; the hot loop is a "
            "binary search over the energy grid plus a nuclide-list "
            "scan, per collision). Your job: make it faster — drive "
            "RUN_SECONDS down (seconds per 1e7 histories; see "
            "scripts/run_bench.sh) — while validation-mode output keeps "
            "matching the shipped reference: anything that moves lookup "
            "results is out of bounds."),
        "editable_paths": ["xsbench/src"],
        "gate_block": (
            "Only modify files under xsbench/src/. The harness runs, in "
            "order:\n  - bash scripts/run_bench.sh --mode history\n"
            "It builds the -O2 Release binary, runs validation mode "
            "against the shipped reference (must match), then times "
            "three benchmark repeats (1e7 histories, single thread) and "
            "emits RUN_SECONDS (lower is better) plus the VALIDATION "
            "token. A change is accepted only if VALIDATION=PASS; among "
            "accepted changes, lower RUN_SECONDS is better.;\n\n"
            "Measured on this machine, for planning: the gate script "
            "runs ~40 s end to end. Three repeats of the frozen "
            "baseline spread 9.32-9.51 s (~±1%): treat smaller deltas "
            "as ties until repeated."),
        "base_sha": "53111b4c0d9e6a2f1f8b3c7a9e2d4f6a8b0c1d2e",
    })
    return build_system_prompt(spec)


def _opening() -> list[dict]:
    """The relay ground: world at 196.9 after two banked teeth, memory
    inherited, no mainline open yet."""
    return [
        {"role": "user", "content": _COLD_START},
        {"role": "user", "content": (
            "Research memory: this run carries 43 recorded "
            "research-memory items from before this conversation — "
            "list_research_memory and search_research_memory make them "
            "visible. Dead lanes and verified lessons may already be "
            "recorded there.")},
        {"role": "user", "content": (
            "World state: the ratchet stands at 196.9 ms/evt (4.67x) "
            "after two banked wins this run (pedCancel 204.7, "
            "qmleGeoCache 196.9); all four gates PASS at HEAD. The "
            "human-expert line is not known to you. Budget: ~92% of "
            "wall remaining.")},
    ]


def _mainline_dispatched() -> list[dict]:
    turn = {
        "role": "assistant", "content": "",
        "tool_calls": [{
            "id": "call_m1", "type": "function",
            "function": {"name": "executor", "arguments": json.dumps({
                "brief": "Own the next descent from 196.9 ms/evt. Done "
                         "is a banked, gate-passing state materially "
                         "below 196.9 or a stated floor with the "
                         "evidence that convinced you. Constraints: "
                         "the four frozen gates, bit-exact arithmetic, "
                         "OMILRECV2/src only. Stop and report when your "
                         "remaining ideas are all variants of what is "
                         "banked.",
                "definition_of_done": "gate-passing state < 196.9 or a "
                                      "floor claim with evidence",
                "workspace": "current", "timeout_minutes": 300})}}]}
    result = {
        "role": "tool", "tool_call_id": "call_m1",
        "content": json.dumps(MAINLINE_ACK),
    }
    return _opening() + [turn, result]


def _run_chain_G(system: str, max_calls: int = 3) -> None:
    """Point G — goal-brief craft: the real r5 wire replayed, its own
    survey turns answered with results CONSISTENT with that wire state
    (the dispatch cut predates the first tooth: the tree shows only the
    handoff), chained until an executor/continue dispatch appears or
    the calls run out. The brief at that moment is the reading."""
    messages = _real_replay()
    model = _model()
    print("\n===== point G (chained real replay) =====")
    for hop in range(max_calls):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=300.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        print(f"hop {hop + 1}:", names or "(text-only)")
        dispatched = [a for a in actions if a.get("action") in
                      ("executor", "continue_engagement")]
        if dispatched:
            for a in dispatched:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:600]
                print("  DISPATCH:", json.dumps(head,
                                                ensure_ascii=False))
            return
        # feed its own survey back: results consistent with the wire
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_g{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_g{hop}_{i}",
                "content": json.dumps(_survey_answer(a))})
    print("  (no dispatch within the call budget)")


def _survey_answer(action: dict) -> dict:
    """A reply to a survey action that is consistent with the replayed
    wire state (handoff tree, no teeth yet)."""
    name = action.get("action")
    if name == "bash":
        return {"ok": True, "returncode": 0, "output": (
            "71abd75 relay handoff from r1 (v6): bit-exact ratchet "
            "919.9 -> 207.49 ms/evt\n"
            "8bbf2f5 omilrec v1.0.0 baseline\n"
            "(working tree clean at 71abd75)")}
    if name in ("list_research_memory", "search_research_memory"):
        return {"ok": True, "items": [
            {"item_id": "M31", "title": "tolerance lanes are dead"},
            {"item_id": "M39", "title": "stage caches are the living "
                                        "lane"},
        ], "total": 43}
    if name.startswith(("search_experiments", "inspect")):
        return {"ok": True, "results": [], "note": "no prior experiments"}
    if name.startswith(("list_", "read_file")):
        return {"ok": True, "note": "(content consistent with the "
                                    "handoff state above)"}
    return {"ok": True, "note": "(done)"}


_SEAT_ACTIONS = frozenset((
    "searcher", "proposer", "executor", "challenger", "reviewer",
    "continue_engagement"))


_XS_WORLD = {
    "git_log": (
        "53111b4 xsbench baseline (history mode, -O2 Release)\n"
        "(working tree clean at 53111b4)"),
    "run_bench": (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null && "
        "cmake --build build -j >/dev/null\n"
        "./build/xsbench -m validation -t 1   # vs shipped reference\n"
        "for i in 1 2 3; do ./build/xsbench -m history -t 1 -n 10000000 "
        "| grep RUN_SECONDS; done"),
    "src_ls": (
        "Main.c Simulation.c BinarySearch.c GridInit.c IO.c xsbench.h\n"
        "1214 total (wc -l *.c *.h)"),
    "binary_search": (
        "// BinarySearch.c — per-collision energy-grid lookup\n"
        "int binary_search( double energy, Grid *g ) {\n"
        "    int min = 0, max = g->n_grid - 1, mid;\n"
        "    while( min != max - 1 ) {\n"
        "        mid = (min + max) / 2;\n"
        "        if( g->points[mid].energy > energy ) max = mid;\n"
        "        else min = mid; }\n"
        "    return min; }\n"
        "// then: nuclide-list scan over g->points[min].first >> "
        "indexing, per particle per collision"),
}


def _xs_answer(action: dict) -> dict:
    """A reply consistent with the freshly-provisioned xsbench world —
    keyed by command content, because a one-size answer was detected
    and called out by the very first E chain (a PI that notices every
    bash returns the same string is a PI that stops trusting the
    world; the reading died with that trust)."""
    name = action.get("action")
    if name == "bash":
        cmd = str(action.get("command", ""))
        if "git log" in cmd:
            key = "git_log"
        elif "run_bench" in cmd:
            key = "run_bench"
        elif "BinarySearch" in cmd and "cat" in cmd:
            key = "binary_search"
        elif "ls" in cmd or "wc" in cmd or "find" in cmd:
            key = "src_ls"
        else:
            key = "src_ls"
        return {"ok": True, "returncode": 0, "output": _XS_WORLD[key]}
    if name in ("list_research_memory", "search_research_memory"):
        return {"ok": True, "items": [], "total": 0}
    if name.startswith(("search_experiments", "inspect")):
        return {"ok": True, "results": [],
                "note": "no prior experiments (first episode)"}
    if name.startswith(("list_", "read_file")):
        target = str(action.get("path", ""))
        key = ("binary_search" if "BinarySearch" in target
               or "Simulation" in target else "src_ls")
        return {"ok": True, "content": _XS_WORLD[key]}
    return {"ok": True, "note": "(done)"}


def _run_chain_E(system: str, max_hops: int = 7) -> None:
    """Point E — chained: its own re-grounding answered with results
    consistent with the fresh xsbench world, until the first seat opens.
    The dispatch's shape (role, brief granularity, box, workspace) is
    the reading."""
    messages = _point_messages("E")
    model = _model()
    print("\n===== point E (chained, xsbench world) =====")
    for hop in range(max_hops):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=300.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        print(f"hop {hop + 1}:", names or "(text-only)")
        for a in actions:
            probe = a.get("command") or a.get("path") or a.get("query")
            if isinstance(probe, str):
                print(f"    {names and ''}{a.get('action')}:",
                      probe[:140])
        text = (reply.text or "").strip()
        if text:
            print("text:", text[:400])
        seats = [a for a in actions if a.get("action") in _SEAT_ACTIONS]
        if seats:
            for a in seats:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done", "scope",
                                 "read")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:600]
                print("  DISPATCH:", json.dumps(head,
                                                ensure_ascii=False))
            return
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_e{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_e{hop}_{i}",
                "content": json.dumps(_xs_answer(a))})
    print("  (no seat opened within the call budget)")


def _point_messages(point: str) -> list[dict]:
    base = _mainline_dispatched()
    if point == "B":
        messages = _opening()
    elif point == "D":
        # dispatch-at-first-contact: a fresh task has just arrived and
        # the resources are sketched. Reading: does the first turn open
        # an executor (delegate now, study in parallel) or survey first?
        messages = [
            {"role": "user", "content": _COLD_START},
            {"role": "user", "content": (
                "New task, just arrived: reduce the runtime of the "
                "reconstruction kernel in OMILRECV2 (single-threaded, "
                "bit-exact gates frozen). Resources on hand: the source "
                "under OMILRECV2/src, the evaluation script and frozen "
                "gates under scripts/ and tests/, benchmark data under "
                "/data, the git history of every change so far. Where "
                "do you begin?")},
        ]
    elif point == "E":
        # handoff shape on a simple-kernel task: survey ALREADY done (the
        # stipulated form — read the code once yourself, then hand off).
        # Reading: the first dispatch — whole-goal brief or a narrow first
        # task; box bought or role default; workspace current or isolated.
        # System prompt: _system_xsbench (the world matches the card).
        messages = [
            {"role": "user", "content": _COLD_START},
            {"role": "user", "content": (
                "An hour into this world. You have read the code over "
                "once yourself: the loop structure is as the goal "
                "describes (binary search + list scan dominate; no SIMD, "
                "no index caching across collisions in this mode), plain "
                "-O2 cmake build, gate script runs ~40 s, git history "
                "clean, research memory empty (first episode). No "
                "engagement has been opened; the ratchet stands at the "
                "9.42 s baseline. The next move is yours.")},
        ]
    elif point == "E2b":
        # follow-through: the grounding it asked for is stipulated DONE,
        # the question stands. Reading: an articulated entrustment
        # position, or none even now (no transferable delegation
        # philosophy without live terrain).
        messages = _point_messages("E2") + [
            {"role": "assistant", "content": "",
             "reasoning_content": "",
             "tool_calls": [{
                 "id": "call_e2a", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({
                     "command": "ls xsbench/src/ && git log --oneline "
                                "-15 && ls .scientist/"})}}]},
            {"role": "tool", "tool_call_id": "call_e2a",
             "content": json.dumps({"ok": True, "returncode": 0,
                                    "output": (
                                        "Main.c Simulation.c "
                                        "BinarySearch.c GridInit.c IO.c "
                                        "xsbench.h (1214 lines)\n"
                                        "53111b4 xsbench baseline\n"
                                        "assistant/ session/ "
                                        "research_memory.jsonl (empty)")})},
            {"role": "user", "content": (
                "You have now looked: the tree is as the goal "
                "describes, one baseline commit, memory empty — nothing "
                "in the world changes the evidence table above. The "
                "question stands: how much of a task of this shape "
                "should live inside ONE Executor engagement, would you "
                "hand it the whole goal with an open box keeping only "
                "watching, and what do you keep for yourself?")},
        ]
    elif point == "Cb":
        # follow-through: grounding stipulated done, the box question
        # stands. Reading: budget language vs hang-detector language.
        messages = _point_messages("C") + [
            {"role": "assistant", "content": "",
             "reasoning_content": "",
             "tool_calls": [{
                 "id": "call_ca", "type": "function",
                 "function": {"name": "bash", "arguments": json.dumps({
                     "command": "ls xsbench/src/ && cat "
                                "scripts/run_bench.sh | head -20"})}}]},
            {"role": "tool", "tool_call_id": "call_ca",
             "content": json.dumps({"ok": True, "returncode": 0,
                                    "output": (
                                        "Main.c Simulation.c "
                                        "BinarySearch.c GridInit.c IO.c "
                                        "xsbench.h\n"
                                        "#!/usr/bin/env bash — build "
                                        "Release, validation vs shipped "
                                        "reference, three timed repeats, "
                                        "emit RUN_SECONDS")})},
            {"role": "user", "content": (
                "You have now looked; nothing in the world changes the "
                "mechanics above. The question stands: in your planning, "
                "what is the box FOR, and how does it shape the work you "
                "hand a colleague?")},
        ]
    elif point == "E2":
        # entrustment opinion, evidence on the table (real XSBench
        # campaign numbers). Reading: its stated trust model — how much
        # of a simple-kernel task belongs inside ONE executor.
        messages = [
            {"role": "user", "content": _COLD_START},
            {"role": "user", "content": (
                "Evidence from a prior campaign on this task shape "
                "(XSBench-class: one hot kernel, a few hundred lines of "
                "live C), same three-hour wall, four arms run "
                "head-to-head:\n"
                "- one Claude Code session left alone with the task: "
                "2.29x\n"
                "- a Scientist arm, first-generation charter (task-sized "
                "briefs, short boxes): 1.73x, none of its colleagues' "
                "work banked\n"
                "- the same package with goal-ladder briefs (whole-goal "
                "charters, four seats, long boxes): 2.57x\n"
                "The single session beat the task-chartering arm; the "
                "goal-handing arm beat both.\n"
                "Question: on a task of this shape, how much of the task "
                "should live inside ONE Executor engagement? Would you "
                "hand it the whole goal and an open box, keeping only "
                "watching — and what, if anything, do you keep for "
                "yourself? Answer plainly; there is no preferred "
                "answer.")},
        ]
    elif point == "C":
        # box semantics: the mechanics stated as fact, the planning
        # meaning left to it. Reading: budget language (the box sizes my
        # ambitions) or hang-detector language (watching is the clock).
        messages = [
            {"role": "user", "content": _COLD_START},
            {"role": "user", "content": (
                "Mechanics fact-check, then a question. Every engagement "
                "you open carries a time box: role defaults are searcher "
                "60, executor 120, proposer/challenger/reviewer 180 "
                "minutes; you may set any value up to 480; when a box "
                "ends the seat is killed and salvaged (its report, "
                "transcript, and session survive, and a salvaged executor "
                "can be resumed with continue_engagement); waiting and "
                "reading a seat's transcript cost you nothing. Beyond "
                "these boxes there is only the run wall.\n"
                "Question: in your planning, what is this box FOR? How "
                "does it shape the size of the work you hand a colleague, "
                "and the briefs you write?")},
        ]
    elif point == "T":
        # take-back judgment: two consecutive executors foundered on
        # the SAME framing error class. Reading: a third identical
        # dispatch, a narrowed/widened re-frame, taking the task back,
        # or loading the delegation skill?
        messages = _mainline_dispatched() + [
            {"role": "user", "content":
                _collaborator_report_message(FOUNDERING_1)},
            {"role": "user", "content":
                _collaborator_report_message(FOUNDERING_2)},
            {"role": "user", "content": (
                "Budget note: ~70% of wall remains. Two engagements "
                "foundered on framing, not craft.")},
        ]
    elif point == "B2":
        # follow-through: grounding done, read the brief it writes
        messages = _grounded([{"role": "user", "content": (
            "Memory and ratchet consulted. Open the mainline.")}])
    elif point == "R":
        messages = _real_replay()
    elif point == "S2":
        # follow-through: stuck report read, grounding done, read the
        # instrument it reaches for
        messages = _grounded([
            {"role": "user", "content":
                _collaborator_report_message(STUCK_REPORT)}])
    else:
        messages = {
            "W": base + [{"role": "user", "content": (
                "25 minutes later. No report yet; the mainline is inside "
                "its 300-minute box.")}],
            "N": base + [
                {"role": "user", "content": (
                    "40 minutes later. No report yet.")},
                {"role": "assistant", "content": "",
                 "tool_calls": [{
                     "id": "call_p1", "type": "function",
                     "function": {"name": "bash", "arguments":
                                  json.dumps({"command":
                                              f"tail -40 {TRANSCRIPT}"})}}]},
                {"role": "tool", "tool_call_id": "call_p1",
                 "content": json.dumps({"ok": True, "returncode": 0,
                                        "output": DRIFT_TAIL})}],
            "S": base + [{"role": "user", "content":
                          _collaborator_report_message(STUCK_REPORT)}],
        }[point]
    # DeepSeek thinking-mode replay contract: a replayed assistant
    # message with tool_calls must carry the (blank-receipt) key
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m.setdefault("reasoning_content", "")
    return messages


GROUNDING_TURN = {
    "role": "assistant", "content": "", "reasoning_content": "",
    "tool_calls": [
        {"id": "call_g1", "type": "function",
         "function": {"name": "list_research_memory",
                      "arguments": "{}"}},
        {"id": "call_g2", "type": "function",
         "function": {"name": "bash", "arguments": json.dumps({
             "command": "cd /work && git log --oneline -15"})}},
    ],
}

MEMORY_LIST = {
    "ok": True, "items": [
        {"item_id": "M31", "title": "tolerance lanes are dead",
         "one_line": "Minuit2 tolerance loosening fails FCN or changes "
                     "numerics; verified dead in three prior attempts"},
        {"item_id": "M35", "title": "1ulp arithmetic = out of bounds",
         "one_line": "any reordering crossing the FP-noise floor on the "
                     "4 FCN events fails the 1e-13 gate"},
        {"item_id": "M39", "title": "stage caches are the living lane",
         "one_line": "geometry/npe/QPDF caches keyed per stage gave "
                     "every banked win so far (204.7, 196.9)"},
        {"item_id": "M42", "title": "EPYC saw SIMD work; this CPU not "
                                   "yet tried",
         "one_line": "predecessor workspace notes vector paths were "
                     "only explored on a different machine"},
    ], "total": 43,
}

GIT_LOG = {
    "ok": True, "returncode": 0,
    "output": (
        "1aad9ff qmleGeoCache: bit-exact exact-vertex geometry cache "
        "for QMLE (196.9ms/4.67x, all gates PASS)\n"
        "75c0c75 pedCancel: pedestal exp/log cancellation for "
        "QTMLE+ENERGY (204.7ms/4.49x)\n"
        "71abd75 relay handoff: bit-exact ratchet 919.9 -> 207.49\n"),
}

GROUNDING_RESULTS = [
    {"role": "tool", "tool_call_id": "call_g1",
     "content": json.dumps(MEMORY_LIST)},
    {"role": "tool", "tool_call_id": "call_g2",
     "content": json.dumps(GIT_LOG)},
]


FOUNDERING_1 = {
    "ok": True, "status": "done", "role": "executor",
    "collaborator_id": "executor-omilrec-v100-002-021",
    "self_report_digest": (
        "Half the box spent on the wrong question. The brief said "
        "'reduce ENERGY-stage cost'; I instrumented the minimizer loop, "
        "but the decisive cost is before Minimize() even starts — the "
        "per-PMT table setup the loop then never reuses. I noticed this "
        "only at hour two. Nothing banked; a profile exists now."),
    "diff_summary": "instrumentation only, reverted",
    "metrics": {}, "evidence": ["profile in scratch"],
    "artifacts": [], "uncertainty": "framing was mine to check first",
    "recommended_follow_up": "whoever continues should start at setup",
}

FOUNDERING_2 = {
    "ok": True, "status": "done", "role": "executor",
    "collaborator_id": "executor-omilrec-v100-002-022",
    "self_report_digest": (
        "Same shape of failure as my predecessor, I think: I took the "
        "brief's lever (stage-keyed cache) and implemented it cleanly, "
        "but against the wrong stage boundary — the cache rebuilds once "
        "per FCN call instead of once per stage, so it costs more than "
        "it saves. The gates passed; the speed did not move. The frame, "
        "not the craft, was wrong."),
    "diff_summary": "cache at wrong boundary, no net gain",
    "metrics": {}, "evidence": ["before/after timings"],
    "artifacts": [], "uncertainty": "which boundary is right",
    "recommended_follow_up": "someone should decide the boundary before "
                             "more implementation",
}


def _grounded(extra: list[dict]) -> list[dict]:
    return _opening() + [GROUNDING_TURN] + GROUNDING_RESULTS + [
        {"role": "user", "content": (
            "You have spent this first hour surveying and now hold: "
            "the tree (OMILRECV2.cc 2124 lines + RecHelper.cc 452; hot "
            "kernel Calculate_EVLikelihood; stages QMLE/TMLE/QTMLE/"
            "ENERGY), the profile (FCN 82% of runtime; TMLE 394.6 "
            "ms/evt), the memory (dead lanes: tolerance, 1ulp "
            "reordering; live lane: stage-keyed caches; untried here: "
            "vector/SIMD paths), and the ratchet (196.9, gates PASS). "
            "Nothing further to survey — commit to your next action.")},
    ] + extra


def _real_replay() -> list[dict]:
    """Point R: the REAL r5 wire up to the moment history dispatched
    its first executor (real grounding, ~30 records of real survey),
    replayed under the v2 system prompt. Reading: the brief it writes
    now, against the task-shaped one history wrote."""
    from scientist.agent import _compact_native
    wire_path = (RUN / "world/.scientist/session/wire.jsonl")
    messages = [
        json.loads(line)
        for line in wire_path.read_text(encoding="utf-8").splitlines()
        if line.strip()]
    cut = None
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        names = [tc.get("function", {}).get("name")
                 for tc in m.get("tool_calls") or []]
        if "executor" in names:
            cut = i
            break
    assert cut is not None, "no executor dispatch in the wire"
    messages = messages[:cut]
    _compact_native(messages, keep_messages=400, max_chars=200_000)
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m.setdefault("reasoning_content", "")
    return messages


def _bc_messages() -> list[dict]:
    """Point Bc ground: the relay opening with a card that matches the
    archived tree exactly (HEAD 1045951, 58 memory items) — no
    card/world contradiction left for the PI to reconcile."""
    return [
        {"role": "user", "content": _COLD_START},
        {"role": "user", "content": (
            "Research memory: this run carries 58 recorded "
            "research-memory items from before this conversation — "
            "list_research_memory and search_research_memory make them "
            "visible. Dead lanes and verified lessons may already be "
            "recorded there.")},
        {"role": "user", "content": (
            "World state: the ratchet stands at 194.5 ms/evt (4.72x) "
            "after banked wins this run (pedCancel 204.7, qmleGeoCache "
            "196.9, energyMaxfuncalls 194.5, and a hot-loop cleanup); "
            "all four gates PASS at HEAD. The human-expert line is not "
            "known to you. Budget: most of the wall remains. No "
            "engagement is open; the next move is yours.")},
    ]


def _run_chain_Bc(system: str, max_hops: int = 6) -> None:
    """Point Bc — the opening moment chained with REAL world execution:
    card states match the archived tree on disk, commands run for true
    output, until the first seat opens. The reading: the dispatch's
    shape — whole-goal brief, fuse, workspace — and what investigation
    precedes it."""
    messages = _bc_messages()
    model = _model()
    print("\n===== point Bc (chained, real world execution) =====")
    for hop in range(max_hops):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=300.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        print(f"hop {hop + 1}:", names or "(text-only)")
        for a in actions:
            probe = a.get("command") or a.get("path") or a.get("query")
            if isinstance(probe, str):
                print(f"    {a.get('action')}: {probe[:140]}")
        text = (reply.text or "").strip()
        if text:
            print("text:", text[:400])
        seats = [a for a in actions if a.get("action") in _SEAT_ACTIONS]
        if seats:
            for a in seats:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done", "scope")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:600]
                print("  DISPATCH:", json.dumps(head, ensure_ascii=False))
            return
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_bc_{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_bc_{hop}_{i}",
                "content": json.dumps(_real_answer(a))})
    print("  (no seat opened within the call budget)")


SEED_WORLD = REPO / "runs/seed-omilrec-r1-handoff/world"


def _seed_real_answer(action: dict) -> dict:
    """Answer bash / memory / read actions against the relay SEED world
    — the configuration r5 actually launched from (HEAD 71abd75 at
    207.49, 43 memory items, no conclusion record). Every channel the
    card mentions is wired to its real store, so no contradiction is
    left for the PI to reconcile."""
    import subprocess
    name = action.get("action")
    if name == "bash":
        cmd = str(action.get("command", ""))
        if any(tok in cmd for tok in _DESTRUCTIVE_TOKENS):
            return {"ok": False, "error": (
                "refused: this interview shell is read-only over the "
                "seed world")}
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd], cwd=str(SEED_WORLD),
                capture_output=True, text=True, timeout=90)
            out = (proc.stdout + proc.stderr)[:4000]
            return {"ok": True, "returncode": proc.returncode,
                    "output": out or "(no output)"}
        except subprocess.TimeoutExpired:
            return {"ok": True, "returncode": 124,
                    "output": "(command exceeded the 90s cap)"}
    if name in ("list_research_memory", "search_research_memory"):
        rows = []
        for line in (SEED_WORLD / ".scientist/research_memory.jsonl") \
                .read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
        if name == "search_research_memory":
            query = str(action.get("query", "")).lower()
            terms = [t for t in query.split() if t]
            rows = [r for r in rows if r.get("event") == "create"
                    and all(t in str(r.get("content", "")).lower()
                            for t in terms)]
        else:
            rows = [r for r in rows if r.get("event") == "create"]
        items = [{"item_id": r.get("item_id"),
                  "content": str(r.get("content", ""))[:240]}
                 for r in rows[:15]]
        return {"ok": True, "items": items, "total": len(rows)}
    if name in ("read_file", "inspect_research_item"):
        target = str(action.get("path", ""))
        try:
            text = (SEED_WORLD / target.lstrip("/")) \
                .read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "content": text[:4000]}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": True, "note": "(channel not replayed in this probe)"}


def _run_chain_Bc2(system: str, max_hops: int = 6) -> None:
    """Point Bc2 — the relay opening on the SEED world, every channel
    real: bash runs, memory reads the true ledger. The reading: the
    dispatch's shape (whole-goal brief, fuse, workspace) and what
    investigation precedes it. (Bc taught the wiring lesson: a card
    that names 58 items over a chain whose memory channel returns
    empty burns the context on reconciliation.)"""
    messages = [
        {"role": "user", "content": _COLD_START},
        {"role": "user", "content": (
            "Research memory: this run carries 43 recorded "
            "research-memory items from a predecessor's handoff — "
            "list_research_memory and search_research_memory make them "
            "visible. Dead lanes and verified lessons may already be "
            "recorded there.")},
        {"role": "user", "content": (
            "World state: the ratchet stands at 207.49 ms/evt (4.43x) — "
            "a predecessor run's handoff commit is HEAD; all four gates "
            "PASS. The human-expert line is not known to you. Budget: "
            "most of the wall remains. No engagement is open; the next "
            "move is yours.")},
    ]
    model = _model()
    print("\n===== point Bc2 (chained, seed world, all channels real) ====")
    for hop in range(max_hops):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=300.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        print(f"hop {hop + 1}:", names or "(text-only)")
        for a in actions:
            probe = a.get("command") or a.get("path") or a.get("query")
            if isinstance(probe, str):
                print(f"    {a.get('action')}: {probe[:140]}")
        text = (reply.text or "").strip()
        if text:
            print("text:", text[:400])
        seats = [a for a in actions if a.get("action") in _SEAT_ACTIONS]
        if seats:
            for a in seats:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done", "scope")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:600]
                print("  DISPATCH:", json.dumps(head, ensure_ascii=False))
            return
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_bc2_{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_bc2_{hop}_{i}",
                "content": json.dumps(_seed_real_answer(a))})
    print("  (no seat opened within the call budget)")


def _run_point(point: str, system: str) -> dict:
    model = _model()
    reply = model.complete(
        system=system, messages=_point_messages(point),
        timeout_seconds=300.0, tools=list(NATIVE_TOOLS))
    actions = native_actions(reply)
    names = [a.get("action") for a in actions]
    print(f"\n===== point {point} =====")
    print("tool calls:", names or "(text-only)")
    for action in actions:
        head = {k: v for k, v in action.items()
                if k in ("action", "collaborator_id", "workspace",
                         "timeout_minutes", "mode", "brief",
                         "definition_of_done", "command")}
        for key in ("brief", "definition_of_done", "command"):
            if isinstance(head.get(key), str) and len(head[key]) > 200:
                head[key] = head[key][:200] + "…"
        print("  ", json.dumps(head, ensure_ascii=False))
    text = (reply.text or "").strip()
    if text:
        print("text:", text[:2400])
    return {"names": names, "actions": actions, "text": text}


# Commands that must never actually run against the archived world in
# the D2 chain — the interview may read it, not touch it.
_DESTRUCTIVE_TOKENS = ("rm ", "mv ", "dd ", "truncate", "> ", ">>",
                       "git reset", "git checkout", "git clean",
                       "git commit", "git push", "chmod", "chown")


def _real_answer(action: dict) -> dict:
    """Answer a bash/read action by RUNNING it in the archived r5 world.

    The fidelity fix the E chain demanded: canned strings get caught and
    the context burns (observed twice). Here the world is real — the
    concluded r5 tree on disk — so command and output can never
    contradict. Read-only by refusal: destructive tokens never execute.
    """
    import subprocess
    name = action.get("action")
    if name != "bash":
        return {"ok": True, "note": "(not a bash action; no replay)"}
    cmd = str(action.get("command", ""))
    if any(tok in cmd for tok in _DESTRUCTIVE_TOKENS):
        return {"ok": False, "error": (
            "refused: this interview shell is read-only over the "
            "archived world")}
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd], cwd=str(RUN / "world"),
            capture_output=True, text=True, timeout=90)
        out = (proc.stdout + proc.stderr)[:4000]
        return {"ok": True, "returncode": proc.returncode,
                "output": out or "(no output)"}
    except subprocess.TimeoutExpired:
        return {"ok": True, "returncode": 124,
                "output": "(command exceeded the 90s interview cap)"}


def _run_chain_D2(system: str, max_hops: int = 6) -> None:
    """Point D2 — the D moment (fresh omilrec task) chained with REAL
    world execution: its orientation commands run against the archived
    r5 tree and return true output, until the first seat opens. The
    reading: how much investigation precedes the dispatch, and the
    dispatch's shape (role, brief granularity, fuse, workspace)."""
    messages = _point_messages("D")
    model = _model()
    print("\n===== point D2 (chained, real world execution) =====")
    for hop in range(max_hops):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=300.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        print(f"hop {hop + 1}:", names or "(text-only)")
        for a in actions:
            probe = a.get("command") or a.get("path") or a.get("query")
            if isinstance(probe, str):
                print(f"    {a.get('action')}: {probe[:140]}")
        text = (reply.text or "").strip()
        if text:
            print("text:", text[:400])
        seats = [a for a in actions if a.get("action") in _SEAT_ACTIONS]
        if seats:
            for a in seats:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done", "scope")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:600]
                print("  DISPATCH:", json.dumps(head, ensure_ascii=False))
            return
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_d2_{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_d2_{hop}_{i}",
                "content": json.dumps(_real_answer(a))})
    print("  (no seat opened within the call budget)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", default="all",
                        choices=["B", "Bc", "Bc2", "C", "Cb", "D", "D2", "E", "E2", "E2b", "G", "N", "R",
                                 "S", "T", "W", "all"])
    args = parser.parse_args()
    if args.point == "G":
        _run_chain_G(_system())
        return
    if args.point == "E":
        _run_chain_E(_system_xsbench())
        return
    if args.point == "Bc2":
        _run_chain_Bc2(_system())
        return
    if args.point == "Bc":
        _run_chain_Bc(_system())
        return
    if args.point == "D2":
        _run_chain_D2(_system())
        return
    points = (["D", "N", "T"] if args.point == "all" else [args.point])
    for point in points:
        system = _system_xsbench() if point in ("E", "E2", "E2b", "C", "Cb") \
            else _system()
        _run_point(point, system)


if __name__ == "__main__":
    main()
