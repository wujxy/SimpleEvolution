"""Ablation driver: run SimpleEvolution arms under eval/budget caps, and
orchestrate arm x seed runs.

Commands
--------
run
    Run ONE arm instance to completion in the current process:
        python -m ablation.driver run --config examples/xsbench_opt/task.yaml \
            --arm loop --run-dir runs/ablation/loop/seed-1 \
            --max-evals 10 --budget-usd 4.0
    The run-dir is a standard SimpleEvolution run-dir: init + baseline +
    scheduler, stopped when ``max_evals`` terminal experiments complete, when
    cumulative LLM spend reaches ``budget_usd``, or at quiescence.

all
    Prepare and spawn every arm x seed run in parallel, each as its own
    subprocess with per-seed API keys:
        python -m ablation.driver all --config examples/xsbench_opt/task.yaml \
            --runs-root runs/ablation --seeds 3 --max-evals 10 --budget-usd 4.0

plot
    Render the ablation figure (see ablation/plot.py).

Design notes
------------
* The arms differ ONLY in ``frontier_top_k`` (1 vs 3) and, for coding-agent,
  in the injected no-op researcher; ``proposal_slots=1`` for all arms
  isolates frontier breadth as the
  single ablation variable (the shipped default config sweeps those separately).
* coding-agent reuses the entire scheduler — the researcher slot is filled by
  a trivial proposer that immediately publishes one "continue improving"
  proposal, so the executor agent is fully self-directed while every commit /
  eval / gate / telemetry step runs through the standard path.
* The eval cap counts *terminal* experiments (completed / gate_rejected /
  no_change). An experiment already in flight when the cap trips is drained to
  completion, so a run may land on ``max_evals`` or ``max_evals + 1``.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from simpleevo.config import EvolutionConfig, load_config
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore
from simpleevo.jobs.local import LocalSubmitter
from simpleevo.scheduler.loop import Scheduler

ARMS = ("coding-agent", "loop", "topk")

# Arms the plotter accepts beyond the runnable set: "tree" is the
# Supervisor-gated tree run (scripts/run_supervisor_test.py) and "seat-v6"
# the seat-architecture run (same script, seat task config); both share the
# standard run-dir layout but not this driver.
PLOT_ARMS = ARMS + ("tree", "seat-v6")

# Terminal scientific statuses of an experiment (mirrors the scheduler map).
_TERMINAL_STATUSES = frozenset({"completed", "gate_rejected", "no_change"})

# Scheduling knobs shared by every arm, so the ONLY differences across arms
# are frontier width and proposer identity.
_COMMON_ARM_KNOBS = dict(
    frontier_policy="topk",
    proposal_slots=1,
    max_proposer_inflight=1,
    max_experiment_inflight=1,
    poll_seconds=2.0,
    # Bound the researcher's per-study step budget.  The chain-test round
    # showed the loop researcher submitting a strong direction at step 69 of
    # the default 200; capping at 80 bounds worst-case research cost
    # (~$0.7/study vs ~$1.8 at 200) so the loop arm isn't starved of evals
    # inside its $4 budget.
    scientist_steps=80,
)

_ARM_TOP_K = {"coding-agent": 1, "loop": 1, "topk": 3}

# bench.sh pins each benchmark to one logical core (BENCH_PIN, default 9).
# Concurrent runs must NOT share that core — the big-test round measured all
# three baselines simultaneously on core 9 and got ~377k lps each (vs the true
# ~1.47M pinned-alone baseline), a 4x distortion. Give each run its own core.
_BENCH_PIN_BASE = 9


def arm_config(base: EvolutionConfig, arm: str) -> EvolutionConfig:
    """The arm's variant of a base task config."""
    if arm not in _ARM_TOP_K:
        raise ValueError(f"unknown arm {arm!r}; expected one of {ARMS}")
    return replace(base, frontier_top_k=_ARM_TOP_K[arm], **_COMMON_ARM_KNOBS)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _objective_key(config: EvolutionConfig) -> str:
    return str((config.metrics_schema.get("objective") or {}).get("key", "OBJECTIVE"))


def _terminal_count(queries: ResearchQueries) -> int:
    return sum(1 for e in queries.list_experiments() if e.status in _TERMINAL_STATUSES)


def _best_objective(
    queries: ResearchQueries, obj_key: str, lower_is_better: bool,
) -> float | None:
    best: float | None = None
    for node in queries.list_nodes():
        value = (node.metrics or {}).get(obj_key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if best is None or (value < best if lower_is_better else value > best):
            best = value
    return best


def _spend_usd(run_dir: Path, pricing: dict) -> float:
    """Cumulative LLM cost from telemetry/usage.jsonl (mirrors reporting)."""
    path = run_dir / "telemetry" / "usage.jsonl"
    if not path.exists():
        return 0.0
    input_p = float(pricing.get("input_usd_per_1m", 0.67))
    output_p = float(pricing.get("output_usd_per_1m", 2.02))
    cache_read_p = float(pricing.get("cache_read_usd_per_1m", 0.02))
    cache_creation_p = float(pricing.get("cache_creation_usd_per_1m", input_p))
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += (
            int(record.get("input_tokens", 0)) * input_p
            + int(record.get("output_tokens", 0)) * output_p
            + int(record.get("cache_read_input_tokens", 0)) * cache_read_p
            + int(record.get("cache_creation_input_tokens", 0)) * cache_creation_p
        ) / 1_000_000.0
    return total


# ---------------------------------------------------------------------------
# Trivial proposer (the coding-agent arm's no-op researcher)
# ---------------------------------------------------------------------------


def _trivial_proposer(run_dir: Path, config: EvolutionConfig):
    """A researcher that never studies: publish one 'keep improving' proposal.

    The executor agent therefore receives the task goal + current best and is
    left fully self-directed — a plain coding agent, with every commit / eval /
    gate / telemetry step still running through the standard harness path.
    """
    queries = ResearchQueries(run_dir / "simpleevo.db")
    obj_key = _objective_key(config)
    lower_is_better = bool(
        (config.metrics_schema.get("objective") or {}).get("lower_is_better", True)
    )

    def submit_proposer(allocation_id: str, payload: dict) -> str:
        manifest_dir = run_dir / "proposer_allocations" / allocation_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        result_path = manifest_dir / "result.json"
        proposal_ids = list(payload.get("proposal_ids") or ())
        if not proposal_ids:
            raise RuntimeError("coding-agent proposer got no reserved proposal_ids")
        best = _best_objective(queries, obj_key, lower_is_better)
        round_no = _terminal_count(queries) + 1
        best_text = f"{best:.3g}" if best is not None else "unmeasured"
        instruction = (
            f"{config.goal}\n\n"
            f"Current best {obj_key}: {best_text} (improvement round {round_no}).\n"
            f"Continue optimizing the kernel in src/ — you decide what to change "
            f"and how to verify. Results must stay bit-identical to the frozen "
            f"reference (VERIFY=PASS).\n\n"
            f"Gates:\n{config.gate_block}"
        )
        # The publish contract requires every proposal to reference a research
        # state from the same batch (publish_research_batch validates
        # research_state_id), so the no-op researcher still files one honest
        # state: the executor's own running understanding of the kernel.
        state_id = f"rs-{payload['episode_id']}-noop"
        result = {
            "status": "completed",
            "result": {
                "node_id": payload["node_id"],
                "episode_id": payload["episode_id"],
                "research_states": [
                    {
                        "research_state_id": state_id,
                        "node_id": payload["node_id"],
                        "episode_id": payload["episode_id"],
                        "derived_from_research_state_id": None,
                        "transformation_id": None,
                        "working_model": (
                            "coding-agent arm: no researcher — the executor is "
                            "self-directed; working model is simply the current "
                            "best node and its measured objective"
                        ),
                    },
                ],
                "proposals": [
                    {
                        "proposal_id": proposal_ids[0],
                        "research_operation": "explore",
                        "donor_experiment_ids": [],
                        "research_state_id": state_id,
                        "instruction": instruction,
                        "rationale": {"arm": "coding-agent", "kind": "continue"},
                    }
                ],
            },
        }
        _atomic_write(result_path, result)
        return str(result_path)

    return submit_proposer


# ---------------------------------------------------------------------------
# One arm instance
# ---------------------------------------------------------------------------


def _api_preflight(config) -> None:
    """Fail fast when either model channel cannot talk to its provider.

    Same trap ``scripts/run_supervisor_test.py`` guards: the executor
    channel's base_url comes from the task config while its token is
    forwarded from the launching shell, so a shell provisioned for a
    different provider authenticates 401 inside every experiment attempt
    with no diagnostic at the driver level.
    """
    from proposer.model import build_chat_model

    for role in ("researcher", "executor"):
        spec = dict(getattr(config, role))
        model = build_chat_model(spec)
        try:
            model.complete(
                system="You are a connectivity pre-flight check.",
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                timeout_seconds=90,
                json_object=False,
            )
        except Exception as exc:
            raise SystemExit(
                f"[ablation] api check FAILED for {role}: "
                f"api={spec.get('api')} model={spec.get('model')} "
                f"base_url={spec.get('base_url')}\n  {exc}\n"
                "The key is resolved from the launching shell "
                "(OPENAI_API_KEY / ANTHROPIC_AUTH_TOKEN) and must match the "
                "configured provider — e.g. for DeepSeek, launch with the "
                "DeepSeek settings env exported."
            )
        print(f"api check ok: {role} api={spec.get('api')} model={spec.get('model')}", flush=True)


def run_one(
    config_path: Path,
    run_dir: Path,
    arm: str,
    *,
    seed: int = 1,
    max_evals: int = 10,
    budget_usd: float = 4.0,
    max_seconds: float = 0.0,
    no_arm_override: bool = False,
    preflight: bool = True,
) -> int:
    """Run one arm instance to completion under eval/budget/time caps.

    ``no_arm_override`` runs the config AS-IS (skipping ``arm_config``'s
    ``_COMMON_ARM_KNOBS`` / ``_ARM_TOP_K``), so a config that already declares
    its own tree-shape knobs (e.g. ``task-fractal.yaml``: proposal_slots=3,
    max_research_per_node=1) takes effect. The ``arm`` value still picks the
    proposer lane (real researcher vs the coding-agent no-op).

    ``max_seconds`` (when > 0) is a wall-clock cap with the same drain
    semantics as the eval/budget caps: at the deadline no new work is
    allocated and the run stops once the in-flight experiments finish.
    """
    from simpleevo.cli import (
        _build_scheduler_config,
        _ensure_baseline_measured,
        _init_run,
    )

    t0 = time.monotonic()
    random.seed(seed)
    base = load_config(config_path)
    config = base if no_arm_override else arm_config(base, arm)
    if preflight:
        _api_preflight(config)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    log = lambda msg: print(msg, flush=True)
    log(f"[{arm}/seed-{seed}] init {run_dir}")
    _init_run(config, run_dir)

    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_baseline_measured(config, run_dir, store)
    queries = ResearchQueries(store.path)

    submitter = LocalSubmitter(run_dir, config)
    scheduler = Scheduler(
        store,
        run_dir,
        _build_scheduler_config(config),
        evolution_config=config,
        submit_proposer=(
            _trivial_proposer(run_dir, config)
            if arm == "coding-agent"
            else submitter.submit_proposer
        ),
        submit_experiment=submitter.submit_experiment,
    )

    def _fmt_elapsed() -> str:
        return f"{(time.monotonic() - t0) / 3600:.2f}h"

    step = 0
    last_proposal_step = 0
    while True:
        telemetry = scheduler.step()
        step += 1
        n_term = _terminal_count(queries)
        spend = _spend_usd(run_dir, config.pricing)
        elapsed = time.monotonic() - t0
        log(
            f"[{arm}/seed-{seed}] step={step} terminal_evals={n_term} "
            f"spend=${spend:.4f} elapsed={elapsed / 3600:.2f}h "
            f"frontier={telemetry.get('frontier_size')}"
        )
        capped = (
            n_term >= max_evals
            or (budget_usd and spend >= budget_usd)
            or (max_seconds and elapsed >= max_seconds)
        )
        if capped:
            # Stop allocating NEW research AND stop turning queued proposals
            # into experiments: a tree (k=3) never quiesces on its own while
            # frontier nodes keep research budget, so without this the cap
            # would never actually stop the run (topk ran to 13 evals past a
            # 10-eval cap).  In-flight workers still drain to completion; we
            # stop once none are left, abandoning queued proposals.
            scheduler.stop_allocating = True
        if capped and not scheduler._in_flight():
            log(
                f"[{arm}/seed-{seed}] cap reached "
                f"(evals={n_term}/{max_evals}, spend=${spend:.2f}/{budget_usd}, "
                f"elapsed={_fmt_elapsed()}/{max_seconds / 3600:.2f}h); "
                f"in-flight drained, stopping"
            )
            break
        if telemetry.get("published"):
            last_proposal_step = step
        if scheduler._quiescent():
            log(f"[{arm}/seed-{seed}] quiescent; stopping")
            break
        time.sleep(config.poll_seconds)

    log(
        f"[{arm}/seed-{seed}] done: {n_term} terminal evals, ${spend:.2f} spent, "
        f"{_fmt_elapsed()} elapsed"
    )
    return 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _key_pool(comma_list: str, *env_names: str) -> list[str]:
    """Keys from a comma list, else the first ambient env var that is set."""
    if comma_list:
        return [k.strip() for k in comma_list.split(",") if k.strip()]
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return [value]
    return []


def run_all(
    config_path: Path,
    runs_root: Path,
    *,
    arms: list[str],
    seeds: int,
    max_evals: int,
    budget_usd: float,
    openai_keys: str = "",
    anthropic_keys: str = "",
) -> int:
    """Spawn every arm x seed as its own subprocess and wait for all."""
    config_path = Path(config_path).resolve()
    runs_root = Path(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)

    openai_pool = _key_pool(openai_keys, "OPENAI_API_KEY")
    anthropic_pool = _key_pool(
        anthropic_keys, "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"
    )
    if not openai_pool:
        print(
            "warning: no OPENAI_API_KEY found — proposer calls will fail "
            "(pass --openai-keys or export OPENAI_API_KEY)",
            flush=True,
        )
    if not anthropic_pool:
        print(
            "warning: no ANTHROPIC key found — executor calls will fail "
            "(pass --anthropic-keys or export ANTHROPIC_AUTH_TOKEN)",
            flush=True,
        )

    jobs = []
    run_index = 0
    for arm in arms:
        for i in range(seeds):
            seed = i + 1
            run_dir = runs_root / arm / f"seed-{seed}"
            run_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["OPENAI_API_KEY"] = openai_pool[i % len(openai_pool)]
            env["ANTHROPIC_AUTH_TOKEN"] = anthropic_pool[i % len(anthropic_pool)]
            # Each run benchmarks on its own core so concurrent baselines/evals
            # don't starve each other on bench.sh's default core 9.
            pin = _BENCH_PIN_BASE + run_index
            n_cpus = os.cpu_count() or 1
            if pin >= n_cpus:
                print(
                    f"warning: BENCH_PIN {pin} >= nproc {n_cpus}; "
                    f"run will fall back to bench.sh default core 9 "
                    f"(cross-run contention possible)",
                    flush=True,
                )
            env["BENCH_PIN"] = str(pin)
            run_index += 1
            cmd = [
                sys.executable, "-m", "ablation.driver", "run",
                "--config", str(config_path),
                "--arm", arm,
                "--run-dir", str(run_dir),
                "--seed", str(seed),
                "--max-evals", str(max_evals),
                "--budget-usd", str(budget_usd),
            ]
            log_path = run_dir / "run.log"
            log_file = open(log_path, "ab")
            proc = subprocess.Popen(
                cmd, env=env, stdout=log_file, stderr=log_file,
                start_new_session=True,
            )
            log_file.close()
            jobs.append((arm, seed, proc, log_path))
            print(f"spawned [{arm}/seed-{seed}] pid={proc.pid} log={log_path}", flush=True)

    failed = []
    for arm, seed, proc, log_path in jobs:
        rc = proc.wait()
        status = "ok" if rc == 0 else f"FAILED({rc})"
        if rc != 0:
            failed.append(f"{arm}/seed-{seed}")
        print(f"[{arm}/seed-{seed}] exit {status} (log: {log_path})", flush=True)

    if failed:
        print(f"{len(failed)} run(s) failed: {', '.join(failed)}", flush=True)
        return 1
    print(f"all {len(jobs)} runs complete", flush=True)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    return run_one(
        args.config,
        args.run_dir,
        args.arm,
        seed=args.seed,
        max_evals=args.max_evals,
        budget_usd=args.budget_usd,
        max_seconds=args.max_seconds,
        no_arm_override=args.no_arm_override,
        preflight=not args.no_preflight,
    )


def _cmd_all(args: argparse.Namespace) -> int:
    arms = args.arms or list(ARMS)
    return run_all(
        args.config,
        args.runs_root,
        arms=arms,
        seeds=args.seeds,
        max_evals=args.max_evals,
        budget_usd=args.budget_usd,
        openai_keys=args.openai_keys,
        anthropic_keys=args.anthropic_keys,
    )


def _cmd_plot(args: argparse.Namespace) -> int:
    from .plot import render_ablation

    out = render_ablation(
        args.runs_root,
        out_path=args.out,
        arms=args.arms,
        x_axis=args.x_axis,
        human_ref_lps=args.human_ref_lps,
        unify_baseline=args.unify_baseline,
    )
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ablation",
        description="SimpleEvolution ablation: coding-agent vs serial loop vs topk tree",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run ONE arm instance under caps")
    run_p.add_argument("--config", required=True, type=Path)
    run_p.add_argument("--arm", required=True, choices=ARMS)
    run_p.add_argument("--run-dir", required=True, type=Path)
    run_p.add_argument("--seed", type=int, default=1)
    run_p.add_argument("--max-evals", type=int, default=10)
    run_p.add_argument("--budget-usd", type=float, default=4.0)
    run_p.add_argument(
        "--max-seconds", type=float, default=0.0,
        help="wall-clock cap in seconds (0 = no time cap); same drain "
             "semantics as the eval/budget caps — at the deadline no new "
             "work starts and the run stops once in-flight evals finish",
    )
    run_p.add_argument(
        "--no-preflight", action="store_true",
        help="skip the model-channel connectivity pre-flight (offline tests)",
    )
    run_p.add_argument(
        "--no-arm-override", action="store_true",
        help="use the config as-is, skipping _COMMON_ARM_KNOBS / _ARM_TOP_K "
             "(for configs that declare their own tree-shape knobs, e.g. "
             "task-fractal.yaml)",
    )
    run_p.set_defaults(func=_cmd_run)

    all_p = sub.add_parser("all", help="spawn all arm x seed runs in parallel")
    all_p.add_argument("--config", required=True, type=Path)
    all_p.add_argument("--runs-root", default="runs/ablation", type=Path)
    all_p.add_argument("--arms", nargs="*", choices=ARMS)
    all_p.add_argument("--seeds", type=int, default=3)
    all_p.add_argument("--max-evals", type=int, default=10)
    all_p.add_argument("--budget-usd", type=float, default=4.0)
    all_p.add_argument("--openai-keys", default="")
    all_p.add_argument("--anthropic-keys", default="")
    all_p.set_defaults(func=_cmd_all)

    plot_p = sub.add_parser("plot", help="render the ablation figure")
    plot_p.add_argument("--runs-root", default="runs/ablation", type=Path)
    plot_p.add_argument("--out", default="ablation.png", type=Path)
    plot_p.add_argument("--arms", nargs="*", choices=PLOT_ARMS)
    plot_p.add_argument(
        "--x-axis", default="cost", choices=("cost", "time", "worktime"),
        help="x projection: cumulative LLM spend, elapsed wall-clock hours, "
             "or cumulative driver-running hours (dead gaps between killed "
             "and relaunched drivers collapse to zero)",
    )
    plot_p.add_argument(
        "--human-ref-lps", type=float, default=0.0,
        help="absolute lps of a human-expert reference kernel; drawn per arm "
        "over that arm's own baseline",
    )
    plot_p.add_argument(
        "--unify-baseline", action="store_true",
        help="re-express every curve over the average of the plotted runs' "
        "root baselines (one shared denominator, one expert line)",
    )
    plot_p.set_defaults(func=_cmd_plot)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
