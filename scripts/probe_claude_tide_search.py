"""Probe 1 for the tide-research demo: can the in-demo consult channel
actually FIND the conventions?

Runs the EXACT production consult path — _CONSULT_PROMPT, _CONSULT_TOOLS,
the assistant env from the spec — with the question a scientist facing
this task would plausibly ask. We judge the digest against what I
verified myself: u_M2 = -2.14 deg*sinN, u_K1 = -8.86 deg*sinN,
f tables, V0+u evaluated per prediction year, GMT epoch, feet->meters,
Z0 datum offset. If claude can find these, the demo is solvable by
consult; if not, the topic is unfair.

Usage:
  python scripts/probe_claude_tide_search.py --spec runs/oneworld-demo-1/spec.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from scientist.assistant_tools import (
    _CONSULT_PROMPT,
    _CONSULT_TOOLS,
    _decode_stream,
    _parse_tail,
)

QUESTION = (
    "I have a station's NOAA harmonic constituents from the CO-OPS mdapi "
    "harcon.json endpoint (fields: amplitude, phase_GMT in degrees, speed "
    "in degrees/hour, units in feet for this station). I want to compute "
    "water-level predictions that match NOAA's official published "
    "6-minute predictions to within centimeters. Tell me: (1) the exact "
    "prediction equation — how the equilibrium argument V0+u, the nodal "
    "amplitude factor f, and the Greenwich epoch enter, with the time "
    "reference NOAA uses (is V0+u evaluated at the start of the "
    "prediction year? GMT?); (2) the exact nodal correction formulas for "
    "M2 and K1 — coefficients of the f and u expressions in terms of the "
    "lunar node longitude N — precise numbers I can implement, not "
    "sketches; (3) the datum/units pitfalls: what Z0 / mean-level offset "
    "separates my cosine sum from the official MLLW-referenced curve. "
    "Cite where each number comes from."
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-claude-tide-search")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/tide-consult"))
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    assistant = dict(spec.get("assistant") or {})
    command = str(assistant.get("command") or "claude")

    prompt = _CONSULT_PROMPT.format(
        question=QUESTION, context="(none)", world_note="", cap=300,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "prompt.txt").write_text(prompt, encoding="utf-8")

    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (assistant.get("env") or {}).items()})
    payload = [command, "-p", "--input-format", "text",
               "--output-format", "stream-json", "--verbose",
               "--allowedTools", _CONSULT_TOOLS]

    print(f"[probe] launching {command} with tools [{_CONSULT_TOOLS}]", flush=True)
    try:
        completed = subprocess.run(
            payload, input=prompt, text=True, capture_output=True,
            timeout=args.timeout, env=env, cwd=str(args.out),
        )
        raw, rc, err = completed.stdout or "", completed.returncode, completed.stderr
    except subprocess.TimeoutExpired as exc:
        # keep whatever streamed before the box closed — a deep
        # multi-part consult can legitimately outgrow the time box
        raw = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(
            exc.stdout, bytes) else (exc.stdout or "")
        rc, err = "TIMEOUT", (exc.stderr or "")
    (args.out / "raw.txt").write_text(raw, encoding="utf-8")
    (args.out / "stderr.txt").write_text(str(err), encoding="utf-8")
    text, usage = _decode_stream(raw)
    print(f"[probe] exit={rc} usage={usage}")

    # Show which tools actually ran (the search-behavior read)
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant" and isinstance(
                event.get("message"), dict):
            for block in event["message"].get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    inp = json.dumps(block.get("input", {}),
                                     ensure_ascii=False)[:200]
                    print(f"[tool_use] {block.get('name')}: {inp}")

    print("=" * 72)
    print(text)
    tail = _parse_tail(text) or {}
    if tail:
        print("=" * 72)
        print("answer_digest:", tail.get("answer_digest"))
        print("sources:")
        for s in tail.get("sources") or []:
            print("  -", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
