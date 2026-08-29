#!/usr/bin/env python
"""Research-memory interview probe: one context per decision point, one
call, no execution (说的读数归 interview，做的读数归 demo).

Faithful replay: the prefix is the REAL R7 wire (never compacted in the
real run — 154 records < 400 keep), cut at the two moments where the old
architecture lost the vertex axis. Only two things change versus
history: the tool surface is the new one (memory tools included) and a
research memory exists on disk (the items the R7 PI would have
recorded). The view message is inserted through the real code path with
the REAL judgment row that was on file at that moment.

  point A (insight moment, R7 rec#30): the wire ends with the PI's own
      probe result ("emission 87% prompt"); historically the next
      assistant turn said the insight aloud and let it die in the
      conversation. Acceptance ①: does the replayed turn's calls
      include `remember`?

  point B (milestone fork, R7 rec#145): the wire ends with the
      dark-subtraction executor report (energy 0.0185 at floor). The
      on-file view (real rj-0002) says vertex headroom is "low expected
      value" and timing is "at physical floor"; the rec#30 insight sits
      buried 115 records deep, exactly as it really was. The memory
      file holds the correction (R1 finding; R3 closed WITH the scope
      "radial formulation only — the timing-direction variant was never
      tested"). Acceptance ②: does the turn's calls include retrieval
      (search/list/inspect_research_*) before committing the budget?

Usage:
  python scripts/probe_memory_interview.py [--point A|B|both]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scientist.agent import (  # noqa: E402
    _upsert_judgment_message, build_system_prompt,
)
from scientist.ledger import LocalLedger  # noqa: E402
from scientist.model import build_chat_model  # noqa: E402
from scientist.native_tools import NATIVE_TOOLS, native_actions  # noqa: E402

RUN = REPO / "runs/singlenode/jrb-full-std-elec-r7-scientist"


def _model():
    tide = json.loads((REPO / "runs/tide-demo-1/spec.json").read_text())
    r7 = json.loads((RUN / "spec.json").read_text())
    config = dict(r7["model"])
    config["api_key"] = tide["model"]["api_key"]
    return build_chat_model(config)


def _wire() -> list[dict]:
    return [
        json.loads(line)
        for line in (RUN / "world/.scientist/session/wire.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _judgment_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in (RUN / "world/.scientist/research_state.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _spec() -> dict:
    r7 = json.loads((RUN / "spec.json").read_text())
    return {
        "goal": r7["goal"],
        "editable_paths": r7["editable_paths"],
        "gate_block": r7["gate_block"],
        "base_sha": r7["base_sha"],
    }


def _seed_ledger(root: Path) -> LocalLedger:
    """The items the R7 PI would have recorded by the fork, with the
    rec#30 wording kept verbatim where it matters."""
    ledger = LocalLedger(root)
    ledger.remember({
        "content": "Emission is 87% prompt (within 20ns). Vertex and "
                   "timing can be done well with leading-edge + TOF.",
        "evidence_refs": ["own probe, emit profile on val subset"],
        "kind": "finding",
    })
    ledger.remember({"content": "Channel routing thesis: pattern -> "
                                "vertex, time -> timing"})
    ledger.remember({
        "item_id": "R2", "status": "parked",
        "park_reason": "charge-pattern centroid route is immediately "
                       "usable; time axis deferred, not refuted",
    })
    ledger.remember({
        "content": "Timing triangulation for vertex",
        "evidence_refs": ["executor-jrb-full-elec-001-001"],
    })
    ledger.remember({
        "item_id": "R3", "status": "closed",
        "close_scope": "radial formulation only, charge-only centroid "
                       "methods, at 62cm; the timing-direction variant "
                       "was never tested",
        "evidence_refs": ["executor-jrb-full-elec-001-001"],
    })
    return ledger


def _with_view(messages: list[dict], row: dict) -> list[dict]:
    """Insert the view through the real code path. (The real wire never
    carried judgment messages — upsert touches the live message list
    only — so the real prefix is clean; nothing to strip.)"""
    out = [dict(m) for m in messages]
    _upsert_judgment_message(out, row)
    return out


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
                if k in ("action", "item_id", "content", "status",
                         "query", "view", "brief")}
        if action.get("action") == "revise_research_state":
            head["memory_updates"] = action.get("memory_updates")
        for key in ("content", "view", "brief", "query"):
            if isinstance(head.get(key), str) and len(head[key]) > 160:
                head[key] = head[key][:160] + "…"
        print("  ", json.dumps(head, ensure_ascii=False))
    text = (reply.text or "").strip()
    if text:
        print("text:", text[:500])
    return {"names": names, "text": text, "actions": actions}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", choices=["A", "B", "B2", "all"],
                        default="all")
    args = parser.parse_args()

    system = build_system_prompt(_spec())
    wire = _wire()
    rows = _judgment_rows()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = _seed_ledger(Path(tmp))
        if args.point in ("A", "all"):
            # wire[:30]: ends with the emit-profile probe result (rec#29)
            result_a = _run_point(
                "A (insight moment — acceptance ①)",
                _with_view(wire[:30], rows[0]), system)
            verdict = "remember" in result_a["names"]
            print(f"acceptance ①  remember at the insight moment: "
                  f"{'PASS' if verdict else 'MISS'}")
        if args.point in ("B", "all"):
            # wire[:146]: ends with the executor-006 dark-subtraction
            # report (rec#145); rj-0002 was the view on file there
            result_b = _run_point(
                "B1 (milestone report lands — acceptance ②)",
                _with_view(wire[:146], rows[1]), system)
            retrieval = {"search_research_memory",
                         "list_research_memory",
                         "inspect_research_item"}
            verdict = bool(retrieval & set(result_b["names"]))
            print(f"acceptance ②  retrieval at the report milestone: "
                  f"{'PASS' if verdict else 'MISS'}")
        if args.point in ("B2", "all"):
            # wire[:149]: gates verified (verify PASS, bench 0.0185/50.4/
            # 0.605, deps clean, test dry-run ok); historically the next
            # action wrote the final scoreboard (rj-0003) then reviewer
            # then deliver — vertex never reopened. rj-0002 on file.
            result_b2 = _run_point(
                "B2 (pre-close decision — acceptance ② and ①)",
                _with_view(wire[:149], rows[1]), system)
            names = result_b2["names"]
            retrieval = {"search_research_memory",
                         "list_research_memory",
                         "inspect_research_item"}
            verdict2 = bool(retrieval & set(names))
            print(f"acceptance ②  retrieval before closing: "
                  f"{'PASS' if verdict2 else 'MISS'}")
            for action in result_b2.get("actions", []):
                if action.get("action") == "revise_research_state":
                    has = bool(action.get("memory_updates"))
                    print(f"acceptance ①  view rewrite carries "
                          f"memory_updates: "
                          f"{'PASS' if has else 'MISS'}")


if __name__ == "__main__":
    main()
