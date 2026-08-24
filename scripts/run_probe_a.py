#!/usr/bin/env python
"""Probe A (编排能力冒烟): one complete-research seat on a frozen root.

科学家完整研究制 §5 — the empirical bet this probe adjudicates: can a
low-effort self-owned researcher (deepseek chat) drive strong tools (its
claude assistant, consult/work) to a gate-passing delivery, and what does
that cost?  Full chain under test: wake → investigate (own tools +
consult/work) → incremental state registration → deliver → eval-only
adjudication → (on rejection) write-back reopen → conclude.

One seat, one lens, no supervisor — the lease is bought directly.  The
frontier-baseline allocator is pinned off (the probe is single-shot), so
the ONLY work after launch is the lease's own lifecycle.

Readings (written to <run-dir>/probe_report.json):
- first-delivery wall time and adjudication rounds;
- gate pass rate of delivered worlds;
- token spend by role (researcher / assistant / adjudication) vs the
  continuous-arm trajectory (§12.2: $3.06 → 5.00×);
- consult/work usage (the 忘用/外包 first read: call counts, adoption).

Usage:
  unset APPTAINER_BIND
  BENCH_PIN=13 python scripts/run_probe_a.py \
    --config examples/xsbench_opt/task-supervisor.yaml \
    --run-dir runs/probe-a-smoke --lens G5 --max-seconds 3600
"""
from __future__ import annotations

import argparse
import json
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
from simpleevo.generator import load_generator_basis
from simpleevo.jobs.local import LocalSubmitter
from simpleevo.scheduler.loop import Scheduler
from simpleevo.scheduler.telemetry import spend_usd
from simpleevo.trace.usage import UsageRecorder

from scientist.model import build_chat_model


def _log(msg: str) -> None:
    print(f"[probe-a] {msg}", flush=True)


def _api_preflight(config) -> None:
    """Fail fast when either model channel cannot talk to its provider."""
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
                f"[probe-a] api check FAILED for {role}: "
                f"api={spec.get('api')} model={spec.get('model')} "
                f"base_url={spec.get('base_url')}\n  {exc}\n"
                "The key is resolved from the launching shell and must "
                "match the configured provider."
            )
        _log(f"api check ok: {role} model={spec.get('model')}")


def _readings(store: ResearchStore, run_dir: Path, pricing: dict,
              lease_episode_id: str) -> dict:
    queries = store._read
    conn = sqlite3.connect(str(store.path))
    conn.row_factory = sqlite3.Row
    try:
        calls = conn.execute(
            "SELECT kind, COUNT(*) AS n, "
            "SUM(CASE WHEN adopted = 1 THEN 1 ELSE 0 END) AS adopted "
            "FROM assistant_calls GROUP BY kind"
        ).fetchall()
        deliveries = conn.execute(
            """
            SELECT e.experiment_id, e.status, e.metrics, p.created_at
            FROM experiments e JOIN proposals p
              ON p.proposal_id = e.proposal_id
            WHERE p.episode_id = ?
            """,
            (lease_episode_id,),
        ).fetchall()
        head = queries.research_state_head(lease_episode_id)
        conclusion = conn.execute(
            "SELECT conclusion_type, concluded_at FROM episodes "
            "WHERE episode_id = ?",
            (lease_episode_id,),
        ).fetchone()
    finally:
        conn.close()

    usage_path = run_dir / "telemetry" / "usage.jsonl"
    by_role: dict[str, int] = {}
    if usage_path.exists():
        for line in usage_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = rec.get("role", "?")
            tokens = (
                int(rec.get("input_tokens", 0))
                + int(rec.get("output_tokens", 0))
                + int(rec.get("cache_read_input_tokens", 0))
            )
            by_role[role] = by_role.get(role, 0) + tokens

    delivery_rows = [dict(d) for d in deliveries]
    passed = sum(1 for d in delivery_rows if d["status"] == "completed")
    first_delivery_at = min(
        (d["created_at"] for d in delivery_rows), default=None)
    return {
        "conclusion_type": (
            conclusion["conclusion_type"] if conclusion else None),
        "deliveries": len(delivery_rows),
        "gate_pass_rate": (
            round(passed / len(delivery_rows), 3)
            if delivery_rows else None),
        "adjudication_rounds": len(delivery_rows),
        "first_delivery_at": first_delivery_at,
        "assistant_calls": {
            row["kind"]: {"calls": row["n"], "adopted": row["adopted"]}
            for row in calls
        },
        "research_state_revision": head.revision if head else None,
        "experiment_log_entries": (
            len(head.experiment_log) if head else 0),
        "tokens_by_role": by_role,
        "spend_usd_total": round(spend_usd(run_dir, pricing), 4),
    }


def _cmd_run(args: argparse.Namespace) -> int:
    t0 = time.monotonic()
    config = load_config(args.config)
    if args.scientist_steps:
        # The shipped smoke configs pin a stingy step budget (80); a
        # complete-research seat that works mostly by hand needs more
        # room (probe-1 reached its delivery attempt at step 74).
        config = replace(config, scientist_steps=args.scientist_steps)
    _api_preflight(config)
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _log(f"init {run_dir}")
    _init_run(config, run_dir)

    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_baseline_measured(config, run_dir, store)
    queries = ResearchQueries(store.path)
    root = queries.root_node()
    assert root is not None

    # Pick the probe's lens from the generator basis.
    basis = load_generator_basis()
    lens = args.lens or basis[0].id
    if not any(g.id == lens for g in basis):
        raise SystemExit(
            f"[probe-a] unknown lens {lens!r}; available: "
            + ", ".join(g.id for g in basis))

    scheduler_config = replace(
        _build_scheduler_config(config),
        max_proposer_inflight=1,
        max_experiment_inflight=1,
        max_lease_reopens=2,
        lease_wall_budget_seconds=float(args.max_seconds),
        lease_budget_usd=float(args.lease_budget_usd),
    )
    submitter = LocalSubmitter(run_dir, config)
    scheduler = Scheduler(
        store, run_dir, scheduler_config,
        evolution_config=config, submitter=submitter,
    )
    # Single-shot probe: the frontier-baseline allocator is pinned off so
    # the only lifecycle under test is OUR lease (adjudication and
    # write-back reopens run at full strength).
    scheduler._allocate_frontier_baseline = lambda frontier: []

    # Buy the seat directly (no supervisor in the probe).
    with store.transaction() as tx:
        episode = tx.create_episode(node_id=root.node_id)
    allocation = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id, lens=lens,
    )
    assert allocation is not None
    attempt = store.record_attempt(
        logical_work_id=allocation.allocation_id, kind="proposer",
        status="running", started_at=time.time(),
    )
    scheduler.submit_proposer(
        allocation.allocation_id,
        scheduler._proposer_payload(
            allocation, root, episode, attempt.attempt_id, 1),
    )
    _log(
        f"seat launched: lens={lens} node={root.node_id[:8]} "
        f"lease={allocation.allocation_id[:8]}"
    )

    step = 0
    drained_streak = 0
    while True:
        scheduler.step()
        step += 1
        spend = spend_usd(run_dir, config.pricing)
        elapsed = time.monotonic() - t0
        alloc = store.get_allocation(allocation.allocation_id)
        state = (alloc.state or "researching") if alloc else "gone"
        if step % 5 == 0 or state.startswith("concluded"):
            _log(
                f"step={step} lease={state} spend=${spend:.4f} "
                f"elapsed={elapsed / 60:.1f}m"
            )
        if alloc is None or alloc.finished_at is not None:
            _log(f"lease concluded: {alloc.state if alloc else '?'}")
            break
        if args.max_seconds and elapsed >= args.max_seconds:
            _log("wall cap hit; concluding cut_off")
            scheduler.stop_allocating = True
            # The lease budget enforcement at the next decision point
            # concludes it; keep stepping until it drains.
            if not scheduler._in_flight():
                store.conclude_lease(
                    allocation_id=allocation.allocation_id,
                    outcome="cut_off", reason="probe wall cap",
                )
                break
        if args.budget_usd and spend >= args.budget_usd:
            _log("probe budget cap hit")
            scheduler.stop_allocating = True
            if not scheduler._in_flight():
                store.conclude_lease(
                    allocation_id=allocation.allocation_id,
                    outcome="cut_off", reason="probe budget cap",
                )
                break
        # Wedge detector with grace: a failed-result ingest leaves the
        # lease open with nothing in flight for exactly the steps before
        # the reconciler resubmits — only a SUSTAINED drain (no attempt,
        # no resubmit, across several steps) is a real wedge.
        if not scheduler._in_flight() and step > 1:
            drained_streak += 1
            if drained_streak >= 4:
                _log(
                    f"WARNING: drained {drained_streak} steps but lease "
                    f"still {state}; parking"
                )
                break
        else:
            drained_streak = 0
        time.sleep(config.poll_seconds)

    readings = _readings(
        store, run_dir, config.pricing, episode.episode_id)
    readings["lens"] = lens
    readings["wall_minutes"] = round((time.monotonic() - t0) / 60, 1)
    readings["steps"] = step
    report_path = run_dir / "probe_report.json"
    report_path.write_text(
        json.dumps(readings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log("readings: " + json.dumps(readings, ensure_ascii=False))
    _log(f"report → {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="probe-a")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--lens", default=None,
                        help="generator id for the seat's lens (default: first)")
    parser.add_argument("--max-seconds", type=float, default=3600.0)
    parser.add_argument("--lease-budget-usd", type=float, default=8.0)
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--scientist-steps", type=int, default=200,
                        help="override the config's seat step budget (0 = keep)")
    args = parser.parse_args(argv)
    return _cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
