"""Tests that frontier axes are persisted atomically with experiment ingest."""
from __future__ import annotations

import tempfile
from pathlib import Path

from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def test_ingest_persists_frontier_axes():
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
            proposal = tx.create_proposal(
                Proposal(
                    proposal_id="p1",
                    node_id=root.node_id,
                    thread_id=thread.thread_id,
                    instruction="go faster",
                    rationale={},
                    snapshot_ref="",
                    status="queued",
                    created_at=1.0,
                )
            )
            experiment = tx.create_experiment(
                experiment_id="e1",
                proposal_id=proposal.proposal_id,
                parent_node_id=root.node_id,
                status="running",
            )

        config = SchedulerConfig(
            max_proposer_inflight=0,
            max_experiment_inflight=0,
            frontier=FrontierConfig(axes=("total_ms",)),
            poll_seconds=0.0,
        )
        scheduler = Scheduler(store, run_dir, config)

        # No worker ran, but we can ingest the result directly through the store.
        store.ingest_experiment_result(
            experiment_id=experiment.experiment_id,
            result_sha="sha-child",
            metrics={"total_ms": 90.0},
            gate_result=GateDecision({"PATHS": GateResult(True, "")}, True),
            status="completed",
            frontier_config=FrontierConfig(axes=("total_ms",)),
        )

        with store.transaction() as tx:
            rows = tx._conn.execute("SELECT * FROM frontier_axes").fetchall()
            assert len(rows) == 1
            assert rows[0]["axis"] == "total_ms"
            assert rows[0]["value"] == 90.0

            # Child thread should have been forked from the proposal snapshot.
            child = tx._conn.execute(
                "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
            ).fetchone()
            assert child is not None
            threads = tx._conn.execute(
                "SELECT * FROM threads WHERE node_id = ?", (child["node_id"],)
            ).fetchall()
            assert len(threads) == 1
            assert threads[0]["parent_thread_id"] == thread.thread_id
