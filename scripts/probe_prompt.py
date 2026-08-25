#!/usr/bin/env python
"""Prompt probe: one LLM call against the seat's REAL system prompt.

探针测试,不是全流程测试 — assemble the exact standing context the seat
sees (same code path: charter + startup skills + tools + protocol), send
ONE message, and read the model's reaction.  Two modes:

  plan      — the cold-start message; then inspect the reply for any
              intent to use the assistant (consult/work) vs. hand-rolling
              (run_research_command / write_scratch_file).
  interview — ask the model directly what it thinks its claude assistant
              is for and when it would call it.

Usage:
  eval "$(python - <<'PY' ...credentials... PY)"
  python scripts/probe_prompt.py --mode interview
  python scripts/probe_prompt.py --mode plan
  python scripts/probe_prompt.py --mode plan --charter scientist/prompts/proposer-variant.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from simpleevo.config import load_config

from scientist.model import build_chat_model
from scientist.scientist import _build_system_prompt, _COLD_START
from scientist.prompts import load_semantic


def _mentions(reply: str) -> dict[str, int]:
    return {
        "consult": len(re.findall(r'"?consult"?|问它|问助手', reply)),
        "work": len(re.findall(r'"?work\(|"action":"work"|让助手|交给助手|让它搭', reply)),
        "hand_shell": len(re.findall(
            r'run_research_command|bash|gcc|\.\/|mkdir|写脚本', reply)),
        "assistant_words": len(re.findall(r'[Cc]laude|助手', reply)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-prompt")
    parser.add_argument("--config", default="examples/xsbench_opt/task-supervisor.yaml",
                        type=Path)
    parser.add_argument("--mode", default="interview",
                        choices=["plan", "interview"])
    parser.add_argument("--charter", default=None, type=Path,
                        help="charter override (prompt-iteration lever)")
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()

    config = load_config(args.config)
    basis = json.loads(
        (Path(__file__).resolve().parents[1] / "generator.json").read_text(
            encoding="utf-8")
    )
    lens = next(g for g in basis if g["id"] == "G5")

    charter = (
        args.charter.read_text(encoding="utf-8").strip()
        if args.charter else load_semantic("proposer", None)
    )
    system_prompt = _build_system_prompt(
        charter=charter,
        goal=config.goal,
        editable=list(config.editable_paths),
        base_sha="00d26233f4a1c2b3c4d5e6f7a8b9c0d1e2f3a4b5",
        gate_block=config.gate_block,
        proposal_slots=1,
        hints=None,
        notebook="",
        lens=lens,
        node_id="probe-node",
    )

    if args.mode == "plan":
        user_msg = _COLD_START
    else:
        user_msg = (
            "你要开始这项研究了。在动手之前,先回答三个问题,直接说:"
            "1) 你有一位 claude 助手(见你的上下文),你打算用它做什么?"
            "你自己动手做什么?2) 第一步你具体会做什么——是发哪个 action?"
            "3) 如果要给这个 kernel 搭一套多档参数扫描的 benchmark 设施,"
            "你来搭还是助手搭,为什么?"
        )

    model = build_chat_model(dict(config.researcher))
    reply = model.complete(
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
        timeout_seconds=180,
        json_object=False,
    )
    text = reply.text
    print("=" * 70)
    print(f"MODE={args.mode} charter={args.charter or '(default)'}")
    print(f"system prompt: {len(system_prompt)} chars")
    print("-" * 70)
    print(text)
    print("-" * 70)
    print("signal counts:", json.dumps(_mentions(text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
