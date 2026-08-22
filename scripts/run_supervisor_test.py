#!/usr/bin/env python
"""Run one bounded SimpleEvolution instance exercising the Supervisor + Integrator path.

The plain ``simpleevo run`` CLI wires the full submitter (so Supervisor jobs
and Integrator jobs run) but has no eval/budget cap and a topk tree never
quiesces on its own.  This driver mirrors ``ablation/driver.run_one``'s
cap-and-drain semantics while keeping the real Supervisor/Integrator workers.

Usage:
  BENCH_PIN=9 python scripts/run_supervisor_test.py \
    --config examples/xsbench_opt/task-supervisor.yaml \
    --run-dir runs/supervisor-int-xsbench \
    --max-evals 14 --budget-usd 6.0

Notes:
- The Supervisor is the sole admission gate: each proposer allocation is a
  Supervisor worker decision; Frontier is telemetry / fallback only.
- Integration requests opened by the Supervisor run as request-scoped
  Integrator workers; gate-passed candidates may promote a new epoch.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from pathlib import Path

from simpleevo.cli import (
    _build_scheduler_config,
    _ensure_baseline_measured,
    _init_run,
)
from simpleevo.config import load_config
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore
from simpleevo.jobs.local import LocalSubmitter
from simpleevo.scheduler.loop import Scheduler

_TERMINAL_STATUSES = frozenset({"completed", "gate_rejected", "no_change"})
_EVENT_KINDS = {
    "supervisor_decision_accepted",
    "supervisor_decision_stale",
    "supervisor_decision_rejected",
    "supervisor_stalled",
    "integration_request_created",
    "integration_candidate_rejected",
    "epoch_promoted",
    "integration_candidate_retained",
}


def _terminal_count(queries: ResearchQueries) -> int:
    return sum(
        1 for e in queries.list_experiments() if e.status in _TERMINAL_STATUSES
    )


def _spend_usd(run_dir: Path, pricing: dict) -> float:
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


def _latest_events(queries: ResearchQueries, seen: set[str]) -> list[str]:
    """Return new scheduler_events rows (one-shot, keyed by event_id)."""
    conn = sqlite3.connect(str(queries.path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT event_id, type, payload FROM scheduler_events "
            "ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        event_id = row["event_id"]
        kind = row["type"]
        if kind not in _EVENT_KINDS or event_id in seen:
            continue
        seen.add(event_id)
        payload = json.loads(row["payload"])
        out.append(json.dumps({"event_type": kind, **payload}, ensure_ascii=False))
    return out


def _cmd_run(args: argparse.Namespace) -> int:
    random.seed(args.seed)
    config = load_config(args.config)
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    log = lambda msg: print(msg, flush=True)
    log(f"[supervisor-int] init {run_dir}")
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
        submitter=submitter,
    )

    seen: set[str] = set()
    step = 0
    while True:
        telemetry = scheduler.step()
        step += 1
        n_term = _terminal_count(queries)
        spend = _spend_usd(run_dir, config.pricing)
        for event in _latest_events(queries, seen):
            log(f"[supervisor-int] event {event}")
        log(
            f"[supervisor-int] step={step} terminal_evals={n_term} "
            f"spend=${spend:.4f} frontier={telemetry.get('frontier_size')} "
            f"pub={telemetry.get('published')} int_jobs={telemetry.get('integrator_jobs')} "
            f"exp_jobs={telemetry.get('experiment_jobs')} ingest={telemetry.get('ingested')}"
        )
        capped = n_term >= args.max_evals or (args.budget_usd and spend >= args.budget_usd)
        if capped:
            scheduler.stop_allocating = True
        if capped and not scheduler._in_flight():
            log(
                f"[supervisor-int] cap reached "
                f"(evals={n_term}/{args.max_evals}, spend=${spend:.2f}/{args.budget_usd}); "
                f"in-flight drained, stopping"
            )
            break
        if scheduler._quiescent():
            log(f"[supervisor-int] quiescent; stopping")
            break
        if scheduler._supervisor_stalled() and not scheduler._in_flight():
            log(
                "[supervisor-int] supervisor STALLED: bounded retries exhausted "
                "with an unconsumed evidence batch; parking the run (this is a "
                "failure, not a scientific stop) — see the supervisor_stalled "
                "scheduler event"
            )
            break
        time.sleep(config.poll_seconds)

    log(f"[supervisor-int] done: {n_term} terminal evals, ${spend:.2f} spent")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-supervisor-test")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-evals", type=int, default=14)
    parser.add_argument("--budget-usd", type=float, default=6.0)
    args = parser.parse_args(argv)
    return _cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
