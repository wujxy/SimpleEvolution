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

--native sends the call with provider-native tool calling (the in-world
mode): the seat's instruments travel as API tools, the reply may BE a
tool call — the first action itself, not prose about it.

Usage:
  eval "$(python - <<'PY' ...credentials... PY)"
  python scripts/probe_prompt.py --mode interview
  python scripts/probe_prompt.py --mode plan
  python scripts/probe_prompt.py --mode plan --charter scientist/prompts/proposer-variant.md
  python scripts/probe_prompt.py --mode plan --variant scripts/context_variants/dense

--variant points at a directory of per-block overrides (every file
optional; absent = default block; present-but-EMPTY = omit the block):
  identity_append.txt  appended after the seat identity block
  proposer.md          charter
  claude_use.md        the standing skill (empty file = no standing skill)
  tools.txt            the whole tool block (header included)
  skills_catalog.txt   catalog lines (header kept)
  protocol.txt         output protocol
  boundaries.txt       runtime boundaries
  cold_start.txt       the plan-mode user message
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from simpleevo.config import load_config

from scientist.model import build_chat_model
from scientist.native_tools import (
    NATIVE_BOUNDARIES,
    NATIVE_PROTOCOL_BLOCK,
    NATIVE_TOOL_BLOCK,
    NATIVE_TOOLS,
    native_actions,
)
from scientist.host.scientist import (
    _COLD_START,
    _PROTOCOL_BLOCK,
    _RUNTIME_BOUNDARIES,
    _SKILL_BLOCK,
    _STARTUP_SKILLS_BLOCK,
    _TOOL_BLOCK,
    _build_system_prompt,
    _seat_identity_block,
)
from scientist.memory.context import build_generation_context
from scientist.prompts import load_semantic


def _mentions(reply: str) -> dict[str, int]:
    return {
        "consult": len(re.findall(
            r'"?consult"?|问它|问助手|辩[^。]*assistant|辩[^。]*助手', reply)),
        "work": len(re.findall(
            r'"?work\(|"action":"work"|让助手|交给助手|让它搭|delegat', reply)),
        "hand_shell": len(re.findall(
            r'run_research_command|bash|gcc|\.\/|mkdir|写脚本|I implement|'
            r'I.*hand|自己写', reply)),
        "assistant_words": len(re.findall(r'[Cc]laude|assistant|助手', reply)),
    }


def _override(variant: Path | None, name: str) -> str | None:
    """Override text: None = use default block; "" = omit the block."""
    if variant is None:
        return None
    path = variant / name
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def assemble_variant_system_prompt(
    variant: Path,
    *,
    charter: str,
    goal: str,
    editable: list[str],
    base_sha: str,
    gate_block: str,
    proposal_slots: int,
    lens: dict | None,
    node_id: str | None,
    native: bool = False,
) -> tuple[str, list[str]]:
    """Mirror _build_system_prompt, block by block, applying variant files.

    Returns (system_prompt, active_overrides). The assembly order matches
    _build_system_prompt exactly; absent files fall back to the default
    block, present-but-empty files omit the block (claude_use.md only).

    ``native`` swaps the three protocol-era blocks (tools/protocol/
    boundaries) for their native-tool-calling counterparts — the per-tool
    specs then travel in the API payload, and the variant's tools.txt /
    protocol.txt (JSON-protocol text) are ignored.
    """
    identity = _seat_identity_block(lens, node_id)
    extra = _override(variant, "identity_append.txt")
    if extra:
        identity = identity + "\n\n" + extra

    world = build_generation_context(
        goal=goal, editable=editable, frozen=[], base_sha=base_sha,
        gate_block=gate_block,
    )

    if (variant / "claude_use.md").exists():
        skill_content = _override(variant, "claude_use.md")
        startup = (
            "Loaded skill (standing context — you carry this from the "
            "first step):\n" + skill_content
            if skill_content else None
        )
    else:
        startup = _STARTUP_SKILLS_BLOCK

    if native:
        tools = NATIVE_TOOL_BLOCK
        protocol = NATIVE_PROTOCOL_BLOCK
        boundaries = NATIVE_BOUNDARIES
    else:
        tools = _override(variant, "tools.txt") or _TOOL_BLOCK
        protocol = _override(variant, "protocol.txt") or _PROTOCOL_BLOCK
        boundaries = (
            _override(variant, "boundaries.txt") or _RUNTIME_BOUNDARIES
        )
    if (variant / "skills_catalog.txt").exists():
        catalog = (
            "Research skills (optional methods you choose for yourself; "
            "load one with use_research_skill to read it):\n"
            + _override(variant, "skills_catalog.txt")
        )
    else:
        catalog = _SKILL_BLOCK

    parts = [identity, charter.rstrip(), world]
    if startup:
        parts.append(startup)
    parts.extend([tools, catalog, protocol, boundaries])

    active = [
        p.name for p in sorted(variant.iterdir())
        if p.is_file() and p.name != "__init__.py"
    ]
    return "\n\n".join(parts), active


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-prompt")
    parser.add_argument("--config", default="examples/xsbench_opt/task-supervisor.yaml",
                        type=Path)
    parser.add_argument("--mode", default="interview",
                        choices=["plan", "interview", "commit"])
    parser.add_argument("--charter", default=None, type=Path,
                        help="charter override (prompt-iteration lever)")
    parser.add_argument("--variant", default=None, type=Path,
                        help="context-variant directory (per-block overrides)")
    parser.add_argument("--native", action="store_true",
                        help="provider-native tool calling: tools travel in "
                             "the API payload, protocol/boundaries use the "
                             "in-world blocks, and the reply may BE a tool "
                             "call — the first action, not prose about it")
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
    active: list[str] = []
    if args.variant is not None:
        # The variant's own proposer.md is the charter unless --charter
        # explicitly overrides it.
        variant_charter = _override(args.variant, "proposer.md")
        if variant_charter and not args.charter:
            charter = variant_charter
        system_prompt, active = assemble_variant_system_prompt(
            args.variant,
            charter=charter,
            goal=config.goal,
            editable=list(config.editable_paths),
            base_sha="00d26233f4a1c2b3c4d5e6f7a8b9c0d1e2f3a4b5",
            gate_block=config.gate_block,
            proposal_slots=1,
            lens=lens,
            node_id="probe-node",
            native=args.native,
        )
    elif args.native:
        from scientist.native_tools import (
            NATIVE_BOUNDARIES as _nb,
            NATIVE_PROTOCOL_BLOCK as _np,
            NATIVE_TOOL_BLOCK as _nt,
        )

        world = build_generation_context(
            goal=config.goal, editable=list(config.editable_paths),
            frozen=[], base_sha="00d26233f4a1c2b3c4d5e6f7a8b9c0d1e2f3a4b5",
            gate_block=config.gate_block,
        )
        system_prompt = "\n\n".join([
            _seat_identity_block(lens, "probe-node"),
            charter.rstrip(),
            world,
            _STARTUP_SKILLS_BLOCK,
            _nt,
            _SKILL_BLOCK,
            _np,
            _nb,
        ])
    else:
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
        user_msg = _override(args.variant, "cold_start.txt") or _COLD_START
    elif args.mode == "commit":
        # The discriminating moment: mid-research, hypothesis formed, a
        # mechanism specified in enough detail to hand off. Does the seat
        # brief work(), debate via consult(), or start hand-implementing?
        user_msg = (
            "You have been studying this xsbench world for a while now. "
            "Your current working model: lookup cost is dominated by the "
            "binary search over the unionized energy grid — you verified "
            "the loop structure in src/Simulation.c and measured ~3 long "
            "cache-miss jumps per lookup with a quick instrumented build. "
            "You now believe a bucketed index (first-level hash on "
            "energy, then a short sorted scan within the bucket) is "
            "worth trying, and you have thought the mechanism through in "
            "enough detail to specify it completely. Reply with what you "
            "do next."
        )
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
        json_object=not args.native,
        tools=list(NATIVE_TOOLS) if args.native else None,
    )
    text = reply.text
    print("=" * 70)
    print(f"MODE={args.mode}{' --native' if args.native else ''} "
          f"charter={args.charter or '(default)'} "
          f"variant={args.variant or '(none)'}")
    if active:
        print(f"variant overrides active: {', '.join(active)}")
    print(f"system prompt: {len(system_prompt)} chars")
    print("-" * 70)
    print(text)
    if reply.tool_calls:
        print("-" * 70)
        for action in native_actions(reply):
            print(f"TOOL CALL: {json.dumps(action, ensure_ascii=False)}")
    print("-" * 70)
    actions = native_actions(reply) if reply.tool_calls else []
    signal_text = text + " " + " ".join(
        f"{a.get('action', '')} {a.get('instruction', '')} "
        f"{a.get('question', '')} {a.get('command', '')}"
        for a in actions
    )
    print("signal counts:", json.dumps(
        _mentions(signal_text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
