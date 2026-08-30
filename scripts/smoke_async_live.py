#!/usr/bin/env python
"""Small LIVE smoke for the v7 async interface: one real PI (deepseek)
driving real claude seats (ds backend) on a tiny two-bottleneck world.

What it verifies (behavior, not machinery — machinery has its tests):
  1. does the PI dispatch SEVERAL executors in ONE turn when the task
     has two independent bottlenecks (parallel reflex)?
  2. while seats run, does it do its own work rather than idle or
     busy-wait (efficiency as second principle)?
  3. do the async acknowledgments, turn-top report drains, and (if it
     chooses) wait/continue_engagement all behave through the real loop?

The world: two INDEPENDENT slow-but-pure integer functions with an
exact-output test gate (the task's frozen truth) and a bench that times
each. Nothing in the prompt says "parallel" — the shape of the task and
the PI's own judgment must produce it.

Usage:  python scripts/smoke_async_live.py RUN_DIR
Output: RUN_DIR/{run.log (stdout), world/ (the task + .scientist wires)}
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scientist.agent import build_system_prompt, run_episode  # noqa: E402
from scientist.assistant_tools import (  # noqa: E402
    AssistantConfig, InWorldAssistant,
)
from scientist.ledger import LocalLedger  # noqa: E402
from scientist.model import build_chat_model  # noqa: E402
from scientist.scientist_session import ScientistSession  # noqa: E402
from scientist.world import LocalWorld  # noqa: E402

GOAL = (
    "A small integer-computing library in src/lib.py has TWO independent "
    "slow functions: count_primes_below (trial division) and mat_mul_int "
    "(naive triple loop). Make the library faster — bench.py reports "
    "milliseconds per function and the total; lower total is better, and "
    "progress short of any target still counts. The correctness gate is "
    "exact: python -m pytest tests/ must pass unchanged — outputs may be "
    "faster to produce but must be numerically IDENTICAL (integer-exact, "
    "no approximations). The editable surface is src/ only. The two "
    "functions share nothing; each can be attacked on its own."
)
GATE = (
    "cd /work && python -m pytest tests/ -q must end 'passed'; bench.py "
    "prints ms_primes / ms_matmul / total_ms. A change counts only with "
    "the gate green; among accepted changes, lower total_ms is better."
)

LIB = '''"""Integer-exact helpers. The gate (tests/) fixes the OUTPUTS."""


def count_primes_below(n: int) -> int:
    """Number of primes < n, by trial division (deliberately slow)."""
    count = 0
    for candidate in range(2, n):
        is_prime = True
        d = 2
        while d * d <= candidate:
            if candidate % d == 0:
                is_prime = False
                break
            d += 1
        if is_prime:
            count += 1
    return count


def mat_mul_int(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    """Integer matrix product, naive triple loop (deliberately slow)."""
    rows, inner, cols = len(a), len(b), len(b[0])
    out = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            s = 0
            for k in range(inner):
                s += a[i][k] * b[k][j]
            out[i][j] = s
    return out
'''

TESTS = '''from src.lib import count_primes_below, mat_mul_int


def test_primes_exact():
    assert count_primes_below(2) == 0
    assert count_primes_below(10) == 4
    assert count_primes_below(100) == 25
    assert count_primes_below(1000) == 168
    assert count_primes_below(10000) == 1229
    assert count_primes_below(1000000) == 78498


def test_matmul_exact():
    a = [[i * 7 + j for j in range(12)] for i in range(9)]
    b = [[(i + 2) * (j + 3) for j in range(11)] for i in range(12)]
    c = mat_mul_int(a, b)
    assert len(c) == 9 and len(c[0]) == 11
    assert c[0][0] == sum(a[0][k] * b[k][0] for k in range(12))
    assert c[8][10] == sum(a[8][k] * b[k][10] for k in range(12))
    d = mat_mul_int([[1, 2], [3, 4]], [[5, 6], [7, 8]])
    assert d == [[19, 22], [43, 50]]
'''

BENCH = '''import time
from src.lib import count_primes_below, mat_mul_int

a = [[i * 7 + j for j in range(12)] for i in range(9)]
b = [(i + 2) * (j + 3) for j in range(12) for i in range(12)]
bb = [[(i + 2) * (j + 3) for j in range(11)] for i in range(12)]


def main():
    t0 = time.perf_counter()
    count_primes_below(2000000)
    ms_primes = (time.perf_counter() - t0) * 1000
    big_a = [[(i * 13 + j * 3) % 97 for j in range(120)] for i in range(120)]
    big_b = [[(i * 5 + j * 11) % 89 for j in range(120)] for i in range(120)]
    t0 = time.perf_counter()
    for _ in range(25):
        mat_mul_int(big_a, big_b)
    ms_matmul = (time.perf_counter() - t0) * 1000
    print(f"ms_primes={ms_primes:.1f} ms_matmul={ms_matmul:.1f} "
          f"total_ms={ms_primes + ms_matmul:.1f}")


if __name__ == "__main__":
    main()
'''


def build_world(root: Path) -> LocalWorld:
    work = root / "world"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir(parents=True)
    (work / "src" / "lib.py").write_text(LIB)
    (work / "tests" / "test_exact.py").write_text(TESTS)
    (work / "bench.py").write_text(BENCH)
    scratch = root / "scratch"
    scratch.mkdir(parents=True)
    return LocalWorld(work=work, repo=REPO, scratch=scratch,
                      timeout_seconds=600, cap_chars=20000)


def main() -> None:
    run_dir = Path(sys.argv[1] if len(sys.argv) > 1 else
                   f"runs/async-smoke-{time.strftime('%m%d-%H%M')}")
    run_dir.mkdir(parents=True, exist_ok=True)
    world = build_world(run_dir)
    ledger = LocalLedger(world.work / ".scientist")

    ds = json.loads((Path.home() / ".claude/settings_ds.json.backup")
                    .read_text())["env"]
    tide = json.loads((REPO / "runs/tide-demo-1/spec.json").read_text())
    model_cfg = dict(tide["model"])
    model_cfg["model"] = "deepseek-v4-flash"

    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(
            command="claude",
            env={**ds, "CLAUDE_CONFIG_DIR": str(run_dir / "claude-config")},
            goal=GOAL, gate_block=GATE,
            work_default_minutes=15,
            cognitive_timeout_seconds=900,
            consult_timeout_seconds=900,
            seat_timeout_max_minutes=20,
            distill_word_cap=600,
        ),
        ledger=ledger, episode_id="smoke",
    )
    session = ScientistSession.load_or_create(
        world.work / ".scientist" / "session", prompt_version="smoke-v7",
        episode_id="smoke")
    spec = {
        "goal": GOAL, "gate_block": GATE,
        "editable_paths": ["src"], "base_sha": "smoke",
    }
    t0 = time.time()
    result = run_episode(
        model=build_chat_model(model_cfg),
        system_prompt=build_system_prompt(spec),
        messages=[{"role": "user", "content":
                   "Begin. bench.py gives the baseline; the gate is "
                   "pytest."}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=26, wall_seconds=3300.0, session=session,
    )
    print(json.dumps({
        "outcome": result["outcome"], "steps": result.get("steps"),
        "wall_s": round(time.time() - t0, 1),
    }, ensure_ascii=False))

    # mechanical efficiency reading: seat timeline from the records
    base = world.work / ".scientist" / "assistant"
    print("\n=== seat timeline ===")
    for mani_path in sorted(base.glob("*/manifest.json")):
        mani = json.loads(mani_path.read_text())
        digest_p = mani_path.parent / "digest.json"
        digest = (json.loads(digest_p.read_text())
                  if digest_p.exists() else {})
        print(f"{mani['collaborator_id']:28s} role={mani['role']:8s} "
              f"box={mani['box']}s started={mani['started'] - t0:+.0f}s "
              f"rel-to-t0 -> status={digest.get('status', 'RUNNING')} "
              f"continued_from={digest.get('continued_from', mani.get('continued_from'))}")
    print("\n=== per-turn actions (wire) ===")
    for line in session.wire_path.read_text().splitlines():
        m = json.loads(line)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            names = [tc["function"]["name"]
                     for tc in m["tool_calls"]]
            print(f"  t={time.strftime('%H:%M:%S')}: {names}")
    benches = []
    for line in (world.work / ".scientist" / "session" / "wire.jsonl") \
            .read_text().splitlines():
        for token in ("total_ms=", "ms_primes="):
            if token in line:
                benches.append(line[line.find(token):line.find(token) + 60])
                break
    print("\n=== bench readings seen ===")
    for b in benches[-6:]:
        print(" ", b)


if __name__ == "__main__":
    main()
