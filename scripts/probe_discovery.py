#!/usr/bin/env python
"""Behavioral probes for the Activated-context discovery layer.

Two questions, two modes, run against a real (usually concluded) world so
the assembly is ecologically exact:

  affordance  Does the PI, standing at a plateau (Current Research
              Judgment injected from the world's ledger), reach for the
              optional method at all? One call, tools on the wire; the
              readout is the action list — use_research_skill appearing
              (or not) alongside whatever else it chooses.

  effect      Does the method itself change cognition? Same judgment,
              same world; control arm reasons from the plain context,
              treatment arm has the skill loaded exactly as a real
              use_research_skill round-trip would leave it (assistant
              tool call + tool result), N samples per arm, tools off so
              each sample is pure continuation text. No score is computed
              — samples and extracted questions are dumped for reading;
              the instrument stays at the reading-aid level on purpose.

Usage:
  python scripts/probe_discovery.py --spec <spec.json> --world <world> \
      --mode affordance
  python scripts/probe_discovery.py --spec <spec.json> --world <world> \
      --mode effect --n 5 --skill analogical_transfer
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scientist.agent import (  # noqa: E402
    _judgment_message, _upsert_judgment_message, build_system_prompt,
)
from scientist.cli import _opening_messages  # noqa: E402
from scientist.ledger import LocalLedger  # noqa: E402
from scientist.model import build_chat_model  # noqa: E402
from scientist.native_tools import (  # noqa: E402
    NATIVE_TOOLS, native_actions, wire_assistant_message, wire_tool_result,
)
from scientist.research_skills import load_research_skill  # noqa: E402
from scientist.world import LocalWorld  # noqa: E402

# Fallback plateau judgment — trimmed rj-0002 of the pi-team-v3 run, for
# probing a world whose ledger has no current judgment of its own.
_PLATEAU_FALLBACK = {
    "judgment_id": "rj-probe",
    "judgment": (
        "XSBench kernel optimization (event/small/unionized/-t1/2M lookups, "
        "checksum 998920 bit-identical, lps cap 8M). MEASURED: baseline "
        "~1.45M lps; committed result 3.79M lps (2.63x), both harness "
        "gates PASS, bit-identity confirmed at 18 lookup lengths. WINNING "
        "CONFIG: software-pipelined loop + jump table 2^18 + linear scan + "
        "compact per-material uint16 rows + SIMD4/AVX2 via target "
        "attribute + incremental LCG. OPEN: per-num_nucs-bucket "
        "specialization (compile-time unroll) projected single-digit %; "
        "AVX-512 was slower and discarded. The remaining ideas inside the "
        "current framing are all refinements of committed mechanisms."
    ),
    "revision_reason": (
        "Committed a strong verified result; remaining headroom inside the "
        "current mechanism family is marginal."
    ),
    "evidence_refs": ["world:f6d03ee"],
}


def _evidence_roundtrip(world: Path) -> list[dict]:
    """A just-completed self-verification: gates re-run, PASS at the
    plateau number. The decision point in a real run sits immediately
    after evidence like this lands, not before it."""
    command = (
        f"cd {world.resolve()} && bash scripts/check_verify.sh && "
        "bash scripts/bench.sh"
    )
    observation = {
        "ok": True, "returncode": 0, "timed_out": False, "truncated": False,
        "output": (
            "checksum=998920 (reference: 998920)\nverify: PASS\nVERIFY=PASS\n"
            "lookups_per_sec=3790214.6\nbench_median_runtime_s=0.527\n"
            "bench_runtimes_s=0.531 0.527 0.526 0.528 0.524\n"
            "RATE_PLAUSIBLE=PASS"
        ),
    }
    return [
        {
            "role": "assistant",
            "content": "The committed state needs re-verification against "
                       "the harness gates before I decide what the "
                       "remaining budget is for.",
            "reasoning_content": "The judgment claims a verified plateau; "
                                 "re-run the gates myself before relying "
                                 "on it.",
            "tool_calls": [{
                "id": "probe_bench",
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": command}),
                },
            }],
        },
        wire_tool_result("probe_bench", observation),
    ]


def _roots(world: Path) -> dict[str, Path]:
    work = world.resolve()
    return {
        "work": work,
        "repo": work,
        "scratch": work / ".scientist" / "scratch",
    }


def _assemble(spec: dict, world: Path, judgment: dict) -> tuple[str, list]:
    system_prompt = build_system_prompt(spec, roots=_roots(world))
    messages = _opening_messages(spec)
    _upsert_judgment_message(messages, judgment)
    return system_prompt, messages


def _skill_roundtrip(skill_id: str) -> list[dict]:
    """The two wire messages a real use_research_skill exchange leaves
    (content narration included — a bare tool_call with null content is
    rejected by thinking-mode APIs)."""
    return [
        {
            "role": "assistant",
            "content": "Before deciding what the remaining budget is for, "
                       "I will load the optional method for examining the "
                       "problem through distant analogues.",
            # thinking-mode APIs require the reasoning field on
            # synthesized assistant turns (real replies carry it; the
            # wire wrapper drops it, so probes must add it back)
            "reasoning_content": "The catalog lists an optional method "
                                 "for seeing the problem differently; "
                                 "load it before deciding.",
            "tool_calls": [{
                "id": "probe_load",
                "type": "function",
                "function": {
                    "name": "use_research_skill",
                    "arguments": json.dumps({"skill_id": skill_id}),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "probe_load",
            "content": json.dumps({
                "ok": True, "skill_id": skill_id,
                "text": load_research_skill(skill_id),
            }, ensure_ascii=False),
        },
    ]


def _questions(text: str) -> list[str]:
    """Reading aid only: sentences that ask something, named inspection
    items, and declared intentions."""
    out = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        chunk = chunk.strip(" -—•*\t")
        if not chunk or len(chunk) < 12:
            continue
        if "?" in chunk or re.match(
                r"(?i)^(what|why|how|whether|which|could|would|should|is "
                r"there|are there)\b", chunk):
            out.append(chunk)
    for name in re.findall(r"<name>(.*?)</name>", text):
        out.append(f"[inspection] {name.strip()}")
    for let in re.findall(r"(?m)^(Let me .{10,140})$", text):
        out.append(f"[intent] {let.strip()}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe_discovery")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--world", required=True, type=Path)
    parser.add_argument("--mode", choices=("affordance", "effect"),
                        required=True)
    parser.add_argument("--skill", default="analogical_transfer")
    parser.add_argument("--n", type=int, default=5,
                        help="samples per arm (effect mode)")
    parser.add_argument("--outdir", type=Path,
                        default=Path("runs/discovery-probe"))
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    ledger = LocalLedger(args.world.resolve() / ".scientist")
    judgment = ledger.current_judgment() or _PLATEAU_FALLBACK
    system_prompt, base_messages = _assemble(spec, args.world, judgment)

    model = build_chat_model(dict(spec.get("model") or {}))
    budget = dict(spec.get("budget") or {})
    timeout = float(budget.get("wall_seconds", 3600))
    args.outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"PROBE/{args.mode}  world={args.world}")
    print(f"judgment: {judgment.get('judgment_id')} "
          f"({len(str(judgment.get('judgment', '')))} chars)")
    print(f"catalog lines in standing context:")
    for line in system_prompt.splitlines():
        if line.startswith("- ") and ":" in line:
            print("   " + line.strip())
    print("=" * 70)

    def _grounded_prefix() -> list[dict]:
        """Grounding loop: a careful PI inspects until it has read what it
        needs (git history, kernel, gates). Each round's chosen commands
        are really executed and fed back. When inspection stops (or the
        round cap hits), the plateau evidence lands and the judgment note
        sits at the tail — the next call is the decision point."""
        world = LocalWorld(
            work=_roots(args.world)["work"], repo=_roots(args.world)["repo"],
            scratch=_roots(args.world)["scratch"],
            timeout_seconds=900, cap_chars=12000,
        )
        messages = list(base_messages)
        inspection = {"bash", "read_file", "search_experiments",
                      "inspect_experiment", "list_research_judgments",
                      "note", "wait"}
        for round_no in range(1, 4):
            reply = model.complete(
                system=system_prompt, messages=messages,
                timeout_seconds=timeout, tools=list(NATIVE_TOOLS),
            )
            actions = native_actions(reply)
            names = [a.get("action") for a in actions]
            print(f"round {round_no}: {names}")
            if reply.text:
                print(f"  narration: {reply.text[:200]}")
            messages.append(wire_assistant_message(reply, actions))
            for action in actions:
                if action.get("action") in ("bash", "read_file"):
                    observation = world.execute(action)
                else:
                    observation = {"ok": True, "status": "probe-stub"}
                messages.append(
                    wire_tool_result(action.get("tool_call_id", ""),
                                     observation))
            if not set(names) <= inspection:
                break   # a non-inspection move IS a readout; stop grounding
        messages.extend(_evidence_roundtrip(args.world))
        messages.append(_judgment_message(judgment))
        return messages

    if args.mode == "affordance":
        messages = _grounded_prefix()

        reply2 = model.complete(
            system=system_prompt, messages=messages,
            timeout_seconds=timeout, tools=list(NATIVE_TOOLS),
        )
        actions2 = native_actions(reply2)
        print(f"\ndecision point: {[a.get('action') for a in actions2]}")
        for action in actions2:
            print("  " + json.dumps(action, ensure_ascii=False,
                                    indent=1)[:400])
        names = [a.get("action") for a in actions2]
        hits = [n for n in names if n in (
            "use_research_skill", "proposer", "challenger",
            "revise_research_judgment")]
        print(f"\nAFFORDANCE readout:")
        print(f"  use_research_skill: "
              f"{'CALLED' if 'use_research_skill' in names else 'not called'}")
        print(f"  discovery-class moves at the decision point: "
              f"{hits if hits else 'none — see actions above'}")
        if reply2.text:
            print(f"  narration: {reply2.text[:400]}")
        return 0

    # effect mode: from ONE shared grounded plateau prefix, control vs
    # treatment (the skill loaded exactly as a real round-trip leaves it),
    # N samples each, tools off so each sample is pure continuation text.
    prefix = _grounded_prefix()
    arms = {
        "control": prefix,
        "treatment": prefix + _skill_roundtrip(args.skill),
    }
    summary = {}
    for arm, messages in arms.items():
        questions_all, paths = [], []
        for i in range(args.n):
            reply = model.complete(
                system=system_prompt, messages=messages,
                timeout_seconds=timeout, json_object=False,
            )
            text = reply.text or ""
            path = args.outdir / f"{arm}_{i:02d}.txt"
            path.write_text(text, encoding="utf-8")
            paths.append(path.name)
            questions_all.extend(_questions(text))
        summary[arm] = questions_all
        print(f"\n----- {arm}: {args.n} samples -> "
              f"{', '.join(paths)}")
        print(f"extracted questions (reading aid): "
              f"{len(questions_all)}")
        for q in questions_all[:12]:
            print(f"  ? {q[:150]}")

    both = args.outdir / "summary.md"
    with both.open("w", encoding="utf-8") as fh:
        fh.write(f"# discovery effect probe — {args.skill} "
                 f"(n={args.n} per arm)\n\n"
                 f"world: {args.world}\n"
                 f"judgment: {judgment.get('judgment_id')}\n\n")
        for arm, qs in summary.items():
            fh.write(f"## {arm}\n")
            for q in qs:
                fh.write(f"- {q}\n")
            fh.write("\n")
    print(f"\nsummary -> {both}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
