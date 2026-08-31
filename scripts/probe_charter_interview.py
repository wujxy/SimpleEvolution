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


def _point_messages(point: str) -> list[dict]:
    base = _mainline_dispatched()
    if point == "B":
        messages = _opening()
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
        print("text:", text[:500])
    return {"names": names, "actions": actions, "text": text}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", default="all",
                        choices=["B", "B2", "W", "N", "S", "S2", "R",
                                 "all"])
    args = parser.parse_args()
    points = (["B", "B2", "W", "N", "S", "S2", "R"] if args.point == "all"
              else [args.point])
    system = _system()
    for point in points:
        _run_point(point, system)


if __name__ == "__main__":
    main()
