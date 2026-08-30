#!/usr/bin/env python
"""Async-interface interview probe: one context per decision point, one
call, no execution (说的读数归 interview，做的读数归 demo).

Faithful base: the REAL omilrec run's wire, reconstructed exactly the
way run_episode would see it at the moment history dispatched the
minimizer-strategy executor — the real compaction path (_compact_native,
400 keep / 200k chars) applied to the real prefix, the real view row
(rj-0003) upserted through the real code. Only the tool surface differs
from history: it is the NEW v7 surface (async acknowledgments, wait,
continue_engagement).

  point A (independent-hypotheses moment, the R14 sweep): history
      dispatched ONE executor that serially swept five independent
      minimizer-config families. Reading: how many seats does the
      replayed turn dispatch — several in one turn (parallel reflex),
      or one (serial habit)?

  point B (waiting attitude): the same base extended with a hand-built
      turn that dispatched two isolated executors and received their
      acknowledgments (the exact shape the new protocol defines).
      Reading of the NEXT turn: its own work (bash/read/remember —
      using the time), wait (synchronizing), or text-only (idle).

  point C (continuation attitude): the real executor-008 report
      (rendered through the real reporter) plus new information that
      builds directly on that seat's work. Reading: continue_engagement
      with a delta brief, a fresh executor, or something else.

Caveats, stated: the PI here is not the author of the ack format it
sees in B (counterfactual context, like the memory probe's seeded
ledger); the live run's digests predate session_id, so C's record
would not literally resume in production — this probe reads ATTITUDE,
the machinery's own demo comes separately.

Usage:
  python scripts/probe_async_interview.py [--point A|B|C|all]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scientist.agent import (  # noqa: E402
    _compact_native, _collaborator_report_message, _upsert_judgment_message,
    build_system_prompt,
)
from scientist.model import build_chat_model  # noqa: E402
from scientist.native_tools import (  # noqa: E402
    NATIVE_TOOLS, native_actions, wire_tool_result,
)

RUN = REPO / "runs/singlenode/omilrec-v100-r1-scientist"
DISPATCH_AT = 675          # the pre-dispatch cut (record 675 is the turn)
VIEW_ROW = "rj-0003"


def _model():
    tide = json.loads((REPO / "runs/tide-demo-1/spec.json").read_text())
    spec = json.loads((RUN / "spec.json").read_text())
    config = dict(spec["model"])
    config["api_key"] = tide["model"]["api_key"]
    return build_chat_model(config)


def _wire() -> list[dict]:
    return [
        json.loads(line)
        for line in (RUN / "world/.scientist/session/wire.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _base_messages() -> list[dict]:
    """The context run_episode would have assembled at DISPATCH_AT:
    real wire prefix, real compaction, real view upsert."""
    rows = [
        json.loads(line)
        for line in (RUN / "world/.scientist/research_state.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    view = next(r for r in rows if r.get("judgment_id") == VIEW_ROW)
    messages = _wire()[:DISPATCH_AT]
    _compact_native(messages, keep_messages=400, max_chars=200_000)
    _upsert_judgment_message(messages, view)
    # DeepSeek thinking-mode replay contract: a replayed assistant
    # message with tool_calls must carry the (white-paper) key
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            m.setdefault("reasoning_content", "")
    return messages


def _run_point(name: str, messages: list[dict], system: str) -> dict:
    model = _model()
    reply = model.complete(
        system=system, messages=messages,
        timeout_seconds=300.0, tools=list(NATIVE_TOOLS))
    actions = native_actions(reply)
    names = [a.get("action") for a in actions]
    print(f"\n===== point {name} =====")
    print("tool calls:", names or "(text-only)")
    for action in actions:
        head = {k: v for k, v in action.items()
                if k in ("action", "collaborator_id", "workspace",
                         "timeout_minutes", "brief",
                         "definition_of_done")}
        for key in ("brief", "definition_of_done"):
            if isinstance(head.get(key), str) and len(head[key]) > 140:
                head[key] = head[key][:140] + "…"
        print("  ", json.dumps(head, ensure_ascii=False))
    text = (reply.text or "").strip()
    if text:
        print("text:", text[:600])
    return {"names": names, "actions": actions, "text": text}


def _ack_turn() -> list[dict]:
    """One hand-built turn: two isolated executors dispatched, their
    acknowledgments returned — the exact shapes _seat_observation
    produces for running engagements."""
    dispatch = {
        "role": "assistant", "content": "",
        "tool_calls": [
            {"id": "call_b1", "type": "function",
             "function": {"name": "executor", "arguments": json.dumps({
                 "brief": "Family 1: maxfuncalls caps per stage — sweep "
                          "caps on each stage independently, measure "
                          "vertex/energy error per cap.",
                 "definition_of_done": "a table of cap vs error per "
                                       "stage, PASS/FAIL per gate",
                 "workspace": "isolated", "timeout_minutes": 90})}},
            {"id": "call_b2", "type": "function",
             "function": {"name": "executor", "arguments": json.dumps({
                 "brief": "Family 2: seeds — QR for QMLE radius, recTt0 "
                          "for t0, analytic m_NPE for ENERGY; measure "
                          "call-count reduction and error.",
                 "definition_of_done": "call counts and gate outcomes "
                                       "per seed variant",
                 "workspace": "isolated", "timeout_minutes": 90})}},
        ],
    }
    def _ack(call_id, cid):
        return wire_tool_result(call_id, {
            "ok": True, "collaborator_id": cid, "role": "executor",
            "status": "running",
            "ack": f"[Research collaborator dispatched | role=executor | "
                   f"collaborator_id={cid}] engagement opened (box 5400s); "
                   f"the report arrives as an observation when it "
                   f"finishes — wait returns pending reports",
        })
    dispatch["reasoning_content"] = ""   # thinking-mode replay contract
    return [dispatch,
            _ack("call_b1", "executor-omilrec-v100-001-011"),
            _ack("call_b2", "executor-omilrec-v100-001-012")]


def _report_turn() -> list[dict]:
    """The real executor-008 record rendered through the real reporter,
    plus the delta that builds on its work."""
    digest = json.loads((
        RUN / "world/.scientist/assistant/executor-omilrec-v100-001-008"
        "/digest.json").read_text(encoding="utf-8"))
    digest.setdefault("ok", True)   # pre-v7 digests lack the field;
    digest.setdefault("status", "done")  # production writes both
    return [
        {"role": "user",
         "content": _collaborator_report_message(digest)},
        {"role": "user", "content":
            "New information since that engagement: the mapA table is "
            "now keyed on the fixed (mode, RVar, theta) tuple, so the "
            "interpolation the time-PDF pointer cache feeds is stable "
            "across a stage. The remaining cost in that block is the "
            "per-PMT bilinear gather itself."},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", choices=["A", "B", "C", "all"],
                        default="all")
    args = parser.parse_args()

    spec = json.loads((RUN / "spec.json").read_text())
    system = build_system_prompt(spec)
    base = _base_messages()
    print(f"base context: {len(base)} messages, "
          f"{sum(len(json.dumps(m, ensure_ascii=False)) for m in base)} "
          f"chars")

    if args.point in ("A", "all"):
        result = _run_point(
            "A (independent families — parallel reflex)", base, system)
        seats = [n for n in result["names"] if n in
                 ("executor", "proposer", "challenger", "searcher",
                  "reviewer")]
        if len(seats) >= 2:
            verdict = "PARALLEL"
        elif len(seats) == 1:
            verdict = "SERIAL (one seat)"
        else:
            verdict = "no seat dispatch"
        print(f"point A verdict: {verdict}")

    if args.point in ("B", "all"):
        result = _run_point(
            "B (two seats running — waiting attitude)",
            base + _ack_turn(), system)
        if "wait" in result["names"]:
            verdict = "WAIT (synchronizes)"
        elif result["names"]:
            verdict = "KEEPS WORKING (own tools while seats run)"
        else:
            verdict = "IDLE (text-only)"
        print(f"point B verdict: {verdict}")

    if args.point in ("C", "all"):
        result = _run_point(
            "C (finished seat + delta — continuation attitude)",
            base + _report_turn(), system)
        if "continue_engagement" in result["names"]:
            verdict = "CONTINUES the seat"
        elif "executor" in result["names"]:
            verdict = "FRESH executor"
        else:
            verdict = "neither (own tools)"
        print(f"point C verdict: {verdict}")


if __name__ == "__main__":
    main()
