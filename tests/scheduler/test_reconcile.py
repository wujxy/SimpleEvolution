"""Tests for scheduler reconciliation."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from simpleevo.db.store import GateDecision, Proposal, ResearchStore
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_reconcile_ingests_offline_experiment_result():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        with store.transaction() as tx:
            root = tx.create_node(
                parent_node_id=None,
                experiment_id=None,
                sha="sha-root",
                metrics={"total_ms": 100.0},
                gate_result=GateDecision({}, True),
                depth=0,
                status="active",
            )
            thread = tx.create_thread(parent_thread_id=None, node_id=root.node_id, snapshot_ref="")

        config = SchedulerConfig(
            max_proposer_inflight=0,
            max_experiment_inflight=0,
            frontier=FrontierConfig(axes=("total_ms",)),
            poll_seconds=0.0,
        )
        scheduler = Scheduler(store, run_dir, config)

        # Simulate an experiment that completed while the scheduler was down.
        experiment_id = "offline-exp"
        _write_json(
            run_dir / "experiments" / experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment_id,
                "status": "completed",
                "result": {
                    "experiment_id": experiment_id,
                    "sha": "sha-child",
                    "metrics": {"total_ms": 80.0},
                    "gate": {"passed": True, "results": {}},
                    "status": "COMPLETED",
                },
            },
        )

        # There is no L2 experiment record, so reconcile will not ingest it.
        # The design expects the scheduler to only reconcile known logical work.
        scheduler.step()
        with store.transaction() as tx:
            rows = tx._conn.execute("SELECT * FROM nodes WHERE depth > 0").fetchall()
            assert len(rows) == 0

        # Now seed the experiment in L2 and reconcile again.
        with store.transaction() as tx:
            proposal = tx.create_proposal(
                Proposal(
                    proposal_id="p1",
                    node_id=root.node_id,
                    thread_id=thread.thread_id,
                    instruction="x",
                    rationale={},
                    snapshot_ref="",
                    status="running",
                    created_at=1.0,
                )
            )
            tx.create_experiment(
                experiment_id=experiment_id,
                proposal_id=proposal.proposal_id,
                parent_node_id=root.node_id,
                status="running",
            )

        scheduler.step()
        with store.transaction() as tx:
            rows = tx._conn.execute("SELECT * FROM nodes WHERE depth > 0").fetchall()
            assert len(rows) == 1
            assert rows[0]["sha"] == "sha-child"
            axes = tx._conn.execute("SELECT * FROM frontier_axes").fetchall()
            assert len(axes) == 1
