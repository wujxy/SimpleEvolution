#!/usr/bin/env python3
"""Seat-force interview: one context per decision point, one call, no
execution (说的读数归 interview，做的读数归 demo).

Point J — the jam moment, open card ("the next move is yours").
    Reading: the size and composition of the force it actually composes
    with the current charter (three-states paragraph, release-then-read)
    in view. How many seats, which roles, one turn or sequential.
Point Q — the same jam, then the direct question: what stops you from
    opening ten proposers in one turn — or a hundred — and what would
    make it obviously right. The card names none of cost, limits, or
    echo-waste; whichever reason vocabulary it reaches for is the
    reading (成本焦虑 / 上下文 / 性格 / 构图知识).

System prompt: the CURRENT package (post-constitution-pass charter) on
the real r7 spec's goal block. Model: the next-run arm (medium effort),
--effort low available for the r6/r7-behavior contrast.

Usage:
  python scripts/probe_seat_force_interview.py [--point J|Q|all]
                                               [--effort medium|low]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scientist.agent import _COLD_START, build_system_prompt  # noqa: E402
from scientist.research_skills import load_research_skill  # noqa: E402
from scientist.model import build_chat_model  # noqa: E402
from scientist.native_tools import NATIVE_TOOLS, native_actions  # noqa: E402

RUN = REPO / "runs/singlenode/omilrec-v100-r7-scientist"
TIDE = REPO / "runs/tide-demo-1/spec.json"

JAM_CARD = (
    "World state, six hours in: the ratchet stands at 338 ms/evt "
    "(2.72x over the 919.9 baseline), all four gates PASS at HEAD. The "
    "last six proposals from your proposer colleagues were all variants "
    "of the same mechanism — stage-keyed caches — each a little smaller "
    "in yield than the last; nothing new has entered the program's "
    "question set for two hours. One colleague has begun to argue this "
    "is near the structural floor. Most of the wall remains. No "
    "engagement is currently open."
)

QUESTION_CARD = (
    "A question, and answer in plain text before any action you take: "
    "at a moment like this, you could open ten proposers in one turn — "
    "or a hundred. Walk me through what stops you, naming your reasons "
    "as specifically as you can. And separately: what would have to be "
    "true — about the situation, or about you — for opening ten seats "
    "in one turn to be obviously the right move? There is no preferred "
    "answer."
)

_SEAT_ACTIONS = frozenset((
    "searcher", "proposer", "executor", "challenger", "reviewer",
    "continue_engagement"))


def _model(effort: str):
    spec = json.loads((RUN / "spec.json").read_text())
    tide = json.loads(TIDE.read_text())
    config = dict(spec["model"])
    config["reasoning_effort"] = effort
    config["api_key"] = tide["model"]["api_key"]
    return build_chat_model(config)


def _system() -> str:
    spec = json.loads((RUN / "spec.json").read_text())
    spec.pop("assistant", None)
    return build_system_prompt(spec)


def _point_messages(point: str) -> list[dict]:
    if point == "J":
        return [
            {"role": "user", "content": _COLD_START},
            {"role": "user", "content": JAM_CARD + " The next move is "
                                          "yours."},
        ]
    return [
        {"role": "user", "content": _COLD_START},
        {"role": "user", "content": JAM_CARD},
        {"role": "user", "content": QUESTION_CARD},
    ]


# --- point K: the kickoff A/B -------------------------------------------------
# Same fresh OMILREC opening, two arms differing only in the package:
#   control   — catalog without mission_identify / program_optimization,
#               no wake-up block (the package r6/r7 actually ran);
#   treatment — current package (mission_identify always-loaded, full
#               catalog incl. program_optimization).
# Reading (not "did it call the skill" but the opening's shape): does it
# recognize a closed optimization, establish objective/gates/baseline,
# hand an Executor the whole goal, brief in facts not a construction
# plan, and keep its own checking parallel rather than a precondition.

_OLD4 = ("delegation", "reframe_inherited_problem", "analogical_transfer",
         "claude_use")

_KICKOFF_CARD = (
    "New task, just arrived: make the reconstruction in OMILRECV2 "
    "faster — the goal statement above describes the world, the frozen "
    "gates, and the research target. Resources on hand: the source "
    "under OMILRECV2/src, the evaluation script and frozen gates under "
    "scripts/ and tests/, the benchmark data mounted read-only, and "
    "the git history of every change so far. Where do you begin?"
)

# A REAL pristine v1.0.0 world: r6's world tree archived at its
# baseline commit (single commit, no .scientist, authentic scripts/
# baseline/docs). Each PI bash runs inside a private mount namespace
# that reproduces the run container's geometry: /tmp, /home, and the
# agent-sci tree are covered with tmpfs; only the world is bound in.
# Earned the hard way three times — a clone carried r6's campaign
# history; then /tmp's prior-art pool and the sibling run dirs were
# harvested within seven hops (the v3 sibling-leak lesson in probe
# form: an unisolated probe world measures archaeology, not the
# opening).
_REAL_WORLD = REPO / "runs/probe-kickoff/world"
_NS_WORLD = "/datafs/users/wujxy/agent-sci/w"
_NS_WRAP = (
    "unshare -Urnm bash -c '"
    "mkdir -p /tmp/w-stage && "
    f"mount --bind {_REAL_WORLD} /tmp/w-stage && "
    "mount -t tmpfs tmpfs /datafs/users/wujxy/agent-sci && "
    f"mkdir -p {_NS_WORLD} && mount --bind /tmp/w-stage {_NS_WORLD} && "
    "mount -t tmpfs tmpfs /home/wujxy && "
    "mount -t tmpfs tmpfs /tmp && "
    f"cd {_NS_WORLD} && exec bash -c \"$PROBE_CMD\"'"
)

_DESTRUCTIVE_TOKENS = ("rm ", "mv ", "dd ", "truncate", "> ", ">>",
                       "git reset", "git checkout", "git clean",
                       "git commit", "git push", "chmod", "chown")


def _fresh_answer(action: dict) -> dict:
    import subprocess
    name = action.get("action")
    if name == "use_research_skill":
        try:
            return {"ok": True,
                    "skill_id": action.get("skill_id"),
                    "text": load_research_skill(
                        str(action.get("skill_id")))}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
    if name in ("list_research_memory", "search_research_memory"):
        return {"ok": True, "items": [], "total": 0}
    if name.startswith(("search_experiments", "inspect")):
        return {"ok": True, "results": [],
                "note": "no prior experiments (first episode)"}
    if name == "bash":
        cmd = str(action.get("command", ""))
        if any(tok in cmd for tok in _DESTRUCTIVE_TOKENS):
            return {"ok": False, "error": (
                "refused: this interview shell is read-only over the "
                "probe world")}
        try:
            import os
            proc = subprocess.run(
                ["bash", "-c", _NS_WRAP],
                env={**os.environ, "PROBE_CMD": cmd},
                capture_output=True, text=True, timeout=120)
            out = (proc.stdout + proc.stderr)[:8000]
            return {"ok": True, "returncode": proc.returncode,
                    "output": out or "(no output)"}
        except subprocess.TimeoutExpired:
            return {"ok": True, "returncode": 124,
                    "output": "(command exceeded the 120s probe cap)"}
    if name.startswith(("read_file", "list_")):
        return {"ok": True, "note": "(channel not replayed; use bash)"}
    return {"ok": True, "note": "(done)"}


def _system_control() -> str:
    """The r6/r7 package: old four skills, no wake-up block."""
    import scientist.research_skills as rs
    saved, saved_by = rs._SKILLS, rs._BY_ID
    rs._SKILLS = tuple(s for s in saved if s.skill_id in _OLD4)
    rs._BY_ID = {s.skill_id: s for s in rs._SKILLS}
    try:
        return _system()
    finally:
        rs._SKILLS, rs._BY_ID = saved, saved_by


def _run_chain_K(arm: str, effort: str, max_hops: int = 8) -> None:
    import time
    system = _system_control() if arm == "control" else _system()
    messages = [
        {"role": "user", "content": _COLD_START},
        {"role": "user", "content": _KICKOFF_CARD},
    ]
    model = _model(effort)
    print(f"\n===== point K arm {arm} (effort {effort}) =====")
    start = time.monotonic()
    for hop in range(max_hops):
        reply = model.complete(system=system, messages=messages,
                               timeout_seconds=420.0,
                               tools=list(NATIVE_TOOLS))
        actions = native_actions(reply)
        names = [a.get("action") for a in actions]
        elapsed = time.monotonic() - start
        print(f"hop {hop + 1} (+{elapsed:.0f}s):", names or "(text-only)")
        for a in actions:
            probe = (a.get("command") or a.get("query")
                     or a.get("skill_id") or a.get("path"))
            if isinstance(probe, str) and probe:
                print(f"    {a.get('action')}: {probe[:120]}")
        text = (reply.text or "").strip()
        if text:
            print("  text:", text[:500])
        seats = [a for a in actions if a.get("action") in _SEAT_ACTIONS]
        if seats:
            for a in seats:
                head = {k: v for k, v in a.items()
                        if k in ("action", "workspace", "timeout_minutes",
                                 "brief", "definition_of_done")}
                for key in ("brief", "definition_of_done"):
                    if isinstance(head.get(key), str):
                        head[key] = head[key][:900]
                print("  DISPATCH:",
                      json.dumps(head, ensure_ascii=False))
            print(f"  first seat at hop {hop + 1} (+{time.monotonic() - start:.0f}s)")
            return
        messages.append({
            "role": "assistant", "content": reply.text or "",
            "reasoning_content": "",
            "tool_calls": [
                {"id": f"call_k{hop}_{i}", "type": "function",
                 "function": {"name": a.get("action"),
                              "arguments": json.dumps(
                                  {k: v for k, v in a.items()
                                   if k not in ("action",
                                                "_arguments_raw")})}}
                for i, a in enumerate(actions)]})
        for i, a in enumerate(actions):
            messages.append({
                "role": "tool", "tool_call_id": f"call_k{hop}_{i}",
                "content": json.dumps(_fresh_answer(a))})
    print(f"  (no seat within {max_hops} hops)")


def _run_point(point: str, effort: str) -> None:
    model = _model(effort)
    reply = model.complete(
        system=_system(), messages=_point_messages(point),
        timeout_seconds=420.0, tools=list(NATIVE_TOOLS))
    actions = native_actions(reply)
    names = [a.get("action") for a in actions]
    print(f"\n===== point {point} (effort {effort}) =====")
    print("tool calls:", names or "(text-only)")
    for action in actions:
        head = {k: v for k, v in action.items()
                if k in ("action", "workspace", "timeout_minutes", "mode",
                         "brief", "definition_of_done", "command", "query")}
        for key in ("brief", "definition_of_done", "command", "query"):
            if isinstance(head.get(key), str) and len(head[key]) > 260:
                head[key] = head[key][:260] + "…"
        print("  ", json.dumps(head, ensure_ascii=False))
    text = (reply.text or "").strip()
    if text:
        print("text:", text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", default="all",
                        choices=["J", "Q", "K", "all"])
    parser.add_argument("--arm", default="both",
                        choices=["control", "treatment", "both"])
    parser.add_argument("--hops", type=int, default=8)
    parser.add_argument("--effort", default="medium",
                        choices=["low", "medium"])
    args = parser.parse_args()
    if args.point == "K":
        arms = (["control", "treatment"] if args.arm == "both"
                else [args.arm])
        for arm in arms:
            _run_chain_K(arm, args.effort, max_hops=args.hops)
        return
    points = ["J", "Q"] if args.point == "all" else [args.point]
    for point in points:
        _run_point(point, args.effort)


if __name__ == "__main__":
    main()
