"""Tests for the infra/scientific split and attempt lifecycle (§16/§17/§18)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed(store: ResearchStore):
    """Create root node + thread + queued proposal + running experiment."""
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
        thread = tx.create_thread(
            parent_thread_id=None, node_id=root.node_id, snapshot_ref=""
        )
        proposal = tx.create_proposal(
            Proposal(
                proposal_id="p1",
                node_id=root.node_id,
                thread_id=thread.thread_id,
                instruction="go faster",
                rationale={},
                snapshot_ref="",
                status="running",
                created_at=1.0,
            )
        )
        experiment = tx.create_experiment(
            experiment_id="e1",
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="running",
        )
        return root, thread, experiment


def test_infra_failed_reopens_experiment_without_scientific_terminal():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, thread, experiment = _seed(store)

        # Record a running attempt for the experiment, then infra-fail it.
        attempt = store.record_attempt(
            logical_work_id=experiment.experiment_id,
            kind="experiment",
            status="running",
            started_at=1.0,
        )
        store.mark_experiment_infra_failed(
            experiment_id=experiment.experiment_id,
            attempt_id=attempt.attempt_id,
        )

        # Experiment is back to pending (re-submittable), no child node created.
        with store.transaction() as tx:
            exp = tx.get_experiment(experiment.experiment_id)
            assert exp.status == "pending"
            children = tx._conn.execute(
                "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
            ).fetchall()
            assert len(children) == 0
            att = tx.get_attempt(attempt.attempt_id)
            assert att.status == "failed"


def test_infra_result_keeps_experiment_open():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, thread, experiment = _seed(store)

        store.record_attempt(
            logical_work_id=experiment.experiment_id,
            kind="experiment",
            status="running",
            started_at=1.0,
        )
        _write_json(
            run_dir / "experiments" / experiment.experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment.experiment_id,
                "status": "failed",
                "result": {
                    "experiment_id": experiment.experiment_id,
                    "outcome": "infra_failed",
                    "reason": "claude timed out",
                },
            },
        )

        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        )
        scheduler.step()

        with store.transaction() as tx:
            exp = tx.get_experiment(experiment.experiment_id)
            assert exp.status == "pending"
            children = tx._conn.execute(
                "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
            ).fetchall()
            assert len(children) == 0


def test_scientific_result_marks_attempt_succeeded_and_creates_child():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, thread, experiment = _seed(store)

        store.record_attempt(
            logical_work_id=experiment.experiment_id,
            kind="experiment",
            status="running",
            started_at=1.0,
        )
        _write_json(
            run_dir / "experiments" / experiment.experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment.experiment_id,
                "status": "completed",
                "result": {
                    "experiment_id": experiment.experiment_id,
                    "outcome": "COMPLETED",
                    "sha": "sha-child",
                    "metrics": {"total_ms": 80.0},
                    "gate": {"passed": True, "results": {}},
                    "changed_paths": ["tinyalgo/__init__.py"],
                },
            },
        )

        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(
                max_proposer_inflight=0,
                max_experiment_inflight=0,
                frontier=FrontierConfig(axes=("total_ms",)),
            ),
        )
        scheduler.step()

        with store.transaction() as tx:
            exp = tx.get_experiment(experiment.experiment_id)
            assert exp.status == "completed"
            assert exp.changed_paths == ("tinyalgo/__init__.py",)
            attempts = store.attempts_for_work(experiment.experiment_id, "experiment")
            assert attempts[-1].status == "succeeded"
            children = tx._conn.execute(
                "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
            ).fetchall()
            assert len(children) == 1


def test_startup_marks_running_attempts_lost():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, thread, experiment = _seed(store)

        attempt = store.record_attempt(
            logical_work_id=experiment.experiment_id,
            kind="experiment",
            status="running",
            started_at=1.0,
        )

        Scheduler(
            store,
            run_dir,
            SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        )

        with store.transaction() as tx:
            att = tx.get_attempt(attempt.attempt_id)
            assert att.status == "lost"
