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
- Before init the driver pre-flights both model channels (researcher and
  executor) with one tiny completion each, failing fast with a diagnostic
  when the launching shell's credentials do not match the configured
  providers (the observed failure: a Claude Code settings.json pointing at
  bigmodel forwards its token into every executor attempt → 401 from
  api.deepseek.com, with no diagnostic at the driver level).
- The Supervisor is the sole admission gate: each proposer allocation is a
  Supervisor worker decision; Frontier is telemetry only (there is no
  fallback — a failing gate retries and may park the run as stalled).
- Integration requests opened by the Supervisor run as request-scoped
  Integrator workers; gate-passed candidates may promote a new epoch.
- Once the eval/budget cap is hit the scheduler only drains: a Supervisor
  result already on disk is closed unapplied
  (supervisor_decision_discarded) and no new gate/integrator work starts.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from dataclasses import replace
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
from simpleevo.scheduler.telemetry import spend_usd

from scientist.model import build_chat_model

_EVENT_KINDS = {
    "supervisor_decision_accepted",
    "supervisor_decision_stale",
    "supervisor_decision_rejected",
    "supervisor_decision_discarded",
    "supervisor_stalled",
    "integration_request_created",
    "integration_candidate_rejected",
    "epoch_promoted",
    "integration_candidate_retained",
}


def _terminal_count(queries: ResearchQueries) -> int:
    # Same scientific-terminal definition as the gate's budget view.
    return queries.terminal_experiment_count()


def _spend_usd(run_dir: Path, pricing: dict) -> float:
    # Thin alias: the shared helper prices the same usage ledger the growth
    # gate's budget view reads, so the cap and the Supervisor's facts never
    # diverge.
    return spend_usd(run_dir, pricing)


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


def _api_preflight(config) -> None:
    """Fail fast when either model channel cannot talk to its provider.

    The executor channel is the trap this guards: its base_url comes from
    the task config while its token is forwarded from the launching shell
    (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY), so a shell provisioned for
    a different provider authenticates 401 inside every experiment attempt
    with no diagnostic at the driver level.
    """
    for role in ("researcher", "executor"):
        spec = dict(getattr(config, role))
        model = build_chat_model(spec)
        try:
            model.complete(
                system="You are a connectivity pre-flight check.",
                messages=[
                    {"role": "user", "content": "Reply with exactly: OK"}
                ],
                timeout_seconds=90,
                json_object=False,
            )
        except Exception as exc:
            raise SystemExit(
                f"[supervisor-int] api check FAILED for {role}: "
                f"api={spec.get('api')} model={spec.get('model')} "
                f"base_url={spec.get('base_url')}\n  {exc}\n"
                "The key is resolved from the launching shell "
                "(OPENAI_API_KEY / ANTHROPIC_AUTH_TOKEN) and must match the "
                "configured provider — e.g. for DeepSeek, launch with the "
                "DeepSeek settings env exported."
            )
        print(
            f"[supervisor-int] api check ok: {role} api={spec.get('api')} "
            f"model={spec.get('model')} base_url={spec.get('base_url')}",
            flush=True,
        )


def _cmd_run(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    random.seed(args.seed)
    config = load_config(args.config)
    _api_preflight(config)
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    log = lambda msg: print(msg, flush=True)
    log(f"[supervisor-int] init {run_dir}")
    _init_run(config, run_dir)

    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_baseline_measured(config, run_dir, store)
    queries = ResearchQueries(store.path)

    submitter = LocalSubmitter(run_dir, config)
    # Durable budget policy: the scheduler installs these limits into the
    # run_limits table where the growth gate reads them; restarting with
    # the same command rebuilds the same state silently.
    scheduler_config = replace(
        _build_scheduler_config(config),
        max_terminal_evals=args.max_evals,
        budget_usd=args.budget_usd,
    )
    scheduler = Scheduler(
        store,
        run_dir,
        scheduler_config,
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
        elapsed = time.monotonic() - t0
        for event in _latest_events(queries, seen):
            log(f"[supervisor-int] event {event}")
        log(
            f"[supervisor-int] step={step} terminal_evals={n_term} "
            f"spend=${spend:.4f} elapsed={elapsed / 3600:.2f}h "
            f"frontier={telemetry.get('frontier_size')} "
            f"pub={telemetry.get('published')} int_jobs={telemetry.get('integrator_jobs')} "
            f"exp_jobs={telemetry.get('experiment_jobs')} ingest={telemetry.get('ingested')}"
        )
        capped = (
            n_term >= args.max_evals
            or (args.budget_usd and spend >= args.budget_usd)
            or (args.max_seconds and elapsed >= args.max_seconds)
        )
        if capped:
            scheduler.stop_allocating = True
        if capped and not scheduler._in_flight():
            log(
                f"[supervisor-int] cap reached "
                f"(evals={n_term}/{args.max_evals}, spend=${spend:.2f}/{args.budget_usd}, "
                f"elapsed={elapsed / 3600:.2f}h/{args.max_seconds / 3600:.2f}h); "
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

    log(
        f"[supervisor-int] done: {n_term} terminal evals, ${spend:.2f} spent, "
        f"{(time.monotonic() - t0) / 3600:.2f}h elapsed"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-supervisor-test")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-evals", type=int, default=14)
    parser.add_argument("--budget-usd", type=float, default=6.0)
    parser.add_argument(
        "--max-seconds", type=float, default=0.0,
        help="wall-clock cap in seconds (0 = no time cap); at the deadline no "
             "new work starts and the run stops once in-flight evals drain",
    )
    args = parser.parse_args(argv)
    return _cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
