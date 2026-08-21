"""Tests for the infra/scientific split and attempt lifecycle (§16/§17/§18)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _seed(store: ResearchStore):
    """Create root node + episode + queued proposal + running experiment."""
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
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id
        )
        proposal = tx.create_proposal(
            Proposal(
                proposal_id="p1",
                node_id=root.node_id,
                episode_id=episode.episode_id,
                instruction="go faster",
                rationale={},
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
        return root, episode, experiment


def test_infra_failed_reopens_experiment_without_scientific_terminal():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, episode, experiment = _seed(store)

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
        root, episode, experiment = _seed(store)

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
        root, episode, experiment = _seed(store)

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
        root, episode, experiment = _seed(store)

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


def test_infra_failed_result_does_not_block_next_attempt():
    """A failed result.json must be consumed so the next reconcile re-submits
    a fresh attempt instead of re-reading the same failed artifact (§18)."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, episode, experiment = _seed(store)

        store.record_attempt(
            logical_work_id=experiment.experiment_id,
            kind="experiment",
            status="running",
            started_at=1.0,
        )
        result_path = run_dir / "experiments" / experiment.experiment_id / "result.json"
        _write_json(
            result_path,
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

        # Step 1: ingest the failed result, mark A1 failed, archive the artifact.
        scheduler.step()
        attempts = store.attempts_for_work(experiment.experiment_id, "experiment")
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert not result_path.exists()

        # Step 2: reconcile sees pending + no result.json → re-submit A2.
        scheduler.step()
        attempts = store.attempts_for_work(experiment.experiment_id, "experiment")
        assert len(attempts) == 2
        assert attempts[0].status == "failed"
        assert attempts[1].status == "running"


def test_proposer_infra_failed_result_does_not_block_next_attempt():
    """A failed proposer result.json must be consumed so the next reconcile
    re-submits a fresh proposer attempt instead of re-reading it (§18)."""
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
            episode = tx.create_episode(
                inherited_from_episode_id=None, node_id=root.node_id
            )

        allocation = store.allocate_proposer(
            node_id=root.node_id,
            episode_id=episode.episode_id,
            proposal_slots=2,
        )
        store.record_attempt(
            logical_work_id=allocation.allocation_id,
            kind="proposer",
            status="running",
            started_at=1.0,
        )
        result_path = (
            run_dir / "proposer_allocations" / allocation.allocation_id / "result.json"
        )
        _write_json(
            result_path,
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "proposer",
                "request_id": allocation.allocation_id,
                "status": "failed",
                "result": {
                    "episode_id": episode.episode_id,
                    "node_id": root.node_id,
                    "outcome": "error",
                    "proposals": [],
                },
                "error": "claude api failure",
            },
        )

        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        )

        # Step 1: ingest the failed proposer result, mark A1 failed, archive.
        scheduler.step()
        attempts = store.attempts_for_work(allocation.allocation_id, "proposer")
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert not result_path.exists()
        assert store.get_allocation(allocation.allocation_id).finished_at is None

        # Step 2: reconcile re-submits a fresh proposer attempt.
        scheduler.step()
        attempts = store.attempts_for_work(allocation.allocation_id, "proposer")
        assert len(attempts) == 2
        assert attempts[0].status == "failed"
        assert attempts[1].status == "running"


def test_invalid_cognitive_payload_fails_attempt_without_partial_rows():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        with store.transaction() as tx:
            root = tx.create_node(
                parent_node_id=None,
                experiment_id=None,
                sha="sha-root",
                metrics={},
                gate_result=GateDecision({}, True),
                depth=0,
                status="active",
            )
            episode = tx.create_episode(node_id=root.node_id)
        allocation = store.allocate_proposer(
            node_id=root.node_id,
            episode_id=episode.episode_id,
            proposal_slots=1,
        )
        attempt = store.record_attempt(
            logical_work_id=allocation.allocation_id,
            kind="proposer",
            status="running",
            started_at=1.0,
        )
        result_path = (
            run_dir / "proposer_allocations" / allocation.allocation_id
            / "result.json"
        )
        state_id = f"rs-{episode.episode_id}-001"
        _write_json(result_path, {
            "status": "completed",
            "result": {
                "episode_id": episode.episode_id,
                "node_id": root.node_id,
                "outcome": "abstain",
                "transformations": [],
                "research_states": [{
                    "research_state_id": state_id,
                    "node_id": root.node_id,
                    "episode_id": episode.episode_id,
                    "derived_from_research_state_id": None,
                    "transformation_id": "ct-missing",
                    "working_model": "A model with a broken reference.",
                    "evidence_refs": [],
                    "created_at": 1.0,
                }],
                "proposals": [],
            },
        })
        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        )

        scheduler.step()

        assert store.attempts_for_work(
            allocation.allocation_id, "proposer"
        )[-1].status == "failed"
        assert store.get_allocation(allocation.allocation_id).finished_at is None
        assert ResearchQueries(store.path).get_research_state(state_id) is None
        assert not result_path.exists()
        assert attempt.attempt_id in next(
            result_path.parent.glob("result.json.*.ingested")
        ).name


def test_episode_is_single_use():
    """A completed episode must not be re-scheduled; a root spreads across its
    fresh episodes, and no fresh episode remains once they are all terminal."""
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
            t1 = tx.create_episode(inherited_from_episode_id=None, node_id=root.node_id)
            t2 = tx.create_episode(inherited_from_episode_id=None, node_id=root.node_id)
            t3 = tx.create_episode(inherited_from_episode_id=None, node_id=root.node_id)

        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        )

        # Each call returns a distinct fresh episode; after deallocating it
        # (episode complete), it is terminal and never returned again.
        picked = []
        for _ in range(3):
            episode = scheduler._idle_episode_for_node(root.node_id)
            assert episode is not None
            assert episode.episode_id not in picked
            picked.append(episode.episode_id)
            alloc = store.allocate_proposer(
                node_id=root.node_id, episode_id=episode.episode_id, proposal_slots=1)
            store.deallocate_proposer(
                allocation_id=alloc.allocation_id, proposals_produced=1)

        # All three episodes are terminal → no fresh episode remains.
        assert scheduler._idle_episode_for_node(root.node_id) is None
