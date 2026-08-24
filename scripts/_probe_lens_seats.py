"""Lens-seat probe: does a generator (透镜) actually change the angle a
proposer opens with, or is it decoration?

Stage-A single-call probe (same shape as _probe_scientist.py): freeze ONE real
wake context — the v5 tree run's ROOT study opening turn, verbatim from its
session file — and run 11 arms: 10 lens seats + 1 no-lens control.  Every arm
shares an identical compact charter; only the seat-identity block differs.
Each arm is asked to state, before any tool use, the question its seat asks
of this world, its first three actions, and why this angle follows from its
assignment.  Production model channel (deepseek-v4-flash, reasoning_effort
as configured = low) so divergence must survive the low-effort regime.

Readout (analysed by hand over scripts/_probe_lens_results.json):
  1. cross-arm divergence of QUESTION (mechanism category spread)
  2. within-arm consistency across the 3 samples
  3. adherence: WHY_THIS_ANGLE names the lens (or the control has none)

Usage: python scripts/_probe_lens_seats.py [samples_per_arm]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from simpleevo.config import load_config
from scientist.model import build_chat_model

RUN_DIR = Path("runs/ablation-v5/tree/seed-1")
# Study 2 (child-world wake): the only v5 episode whose session file records
# the full opening wake turn verbatim.  Richer than the root study's context
# and carries a strong predecessor direction — the anti-anchor condition the
# seat design must survive.
EPISODE = "33d55a3415e04e8aaf7a39c5741d8d92"
CONFIG = "examples/xsbench_opt/task-supervisor-branch.yaml"
LENSES_PATH = Path("scripts/_lenses_v2.json")
OUT_PATH = Path("scripts/_probe_lens_results.json")

# Compact charter shared by EVERY arm (identical bytes; only the seat block
# above it differs).  A thin stand-in for the seat charter of the design doc.
# NOTE (design doc §2.1): no proposal COUNTS appear here — oneness is a
# harness fact (the lease reserves a single proposal id), not identity text;
# numbers re-import portfolio/selection thinking.  Possessive singular only.
COMPACT_CHARTER = """\
You are one research seat assigned to this node. Investigate from your
assigned angle, maintain your understanding of this world, and submit your
proposal — exploring a new direction, or synthesizing inspected donors — or
an empty-seat memo if your angle provably has nothing here. You do not own
the whole problem; you own your question, asked well.
You distinguish what you believe from what you have verified; the harness's
evaluation decides what actually happened. Your proposal needs a reason it
deserves an answer, not a proof. Do not pad it with a second mechanism.
The executor implements; the harness evaluates; you provide the research
judgment."""

PROBE_ASK = """\
Before any tool use, reply in exactly this format and nothing else:
QUESTION: the single question your seat is asking of this world (<=60 words)
FIRST_ACTIONS: your first three investigative actions, one line each
WHY_THIS_ANGLE: one sentence on how this follows from your seat's assignment"""


def seat_system(lens: dict | None) -> str:
    parts = []
    if lens is None:
        parts.append("You are a Scientist assigned to this node.")
    else:
        parts.append(
            f"You are the {lens['id']}（{lens['name_zh']}）seat of this node. "
            "Your lens is your identity: it is the angle you were hired for, "
            "not advice you may weigh."
        )
        parts.append(f"透镜操作指令：{lens['directive']}")
        parts.append(f"透镜禁令：{lens['forbidden']}")
        parts.append(f"提交自检：{lens['self_check']}")
    parts.append(COMPACT_CHARTER)
    return "\n\n".join(parts)


def wake_context() -> str:
    path = RUN_DIR / "episodes" / EPISODE / "session" / "session.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    assert first.get("role") == "user", "session does not open with a user turn"
    content = first["content"]
    assert "Child world" in content or "authoritative" in content, (
        "unexpected wake context; refusing to probe on the wrong text")
    return content


def main() -> int:
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    lenses = json.loads(LENSES_PATH.read_text(encoding="utf-8"))
    arms = [dict(id="control", lens=None)] + [
        dict(id=l["id"], lens=l) for l in lenses
    ]
    context = wake_context()
    model = build_chat_model(dict(load_config(CONFIG).researcher))

    results = []
    for arm in arms:
        for s in range(samples):
            reply = None
            err = None
            for attempt in range(3):
                try:
                    reply = model.complete(
                        system=seat_system(arm["lens"]),
                        messages=[{"role": "user",
                                   "content": context + "\n\n" + PROBE_ASK}],
                        timeout_seconds=180,
                        json_object=False,
                    )
                    break
                except Exception as exc:
                    err = str(exc)
                    time.sleep(5 * (attempt + 1))
            results.append(dict(arm=arm["id"], sample=s,
                                reply=(reply.text if reply else None), usage=(getattr(reply, "usage", None) and str(reply.usage)) if reply else None, error=err))
            got = "ok" if reply else f"ERR({err})"
            first_line = (reply.text if reply else "").strip().splitlines()[0][:100]
            print(f"[{arm['id']}#{s}] {got}: {first_line}", flush=True)

    OUT_PATH.write_text(
        json.dumps(dict(samples=samples, results=results),
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
