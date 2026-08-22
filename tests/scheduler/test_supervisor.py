from pathlib import Path

import pytest

from proposer.supervisor import (
    AllocationDirective,
    SupervisorDecision,
    build_group_snapshot,
    validate_decision,
)
from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.scheduler.frontier import Frontier
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _gate() -> GateDecision:
    return GateDecision({"PASS": GateResult(True, "")}, True)


def test_snapshot_includes_eligible_dormant_node_outside_frontier(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=_gate(), depth=0,
            status="active",
        )
        dormant = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-old", sha="old",
            metrics={"score": 1}, gate_result=_gate(), depth=1,
            status="dormant",
        )
        dead = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-dead", sha="dead",
            metrics={}, gate_result=_gate(), depth=1, status="dead",
        )

    snapshot = build_group_snapshot(
        store, max_research_per_node=3, max_proposals_per_node=9,
    )

    assert {node.node_id for node in snapshot.eligible_nodes} == {
        root.node_id, dormant.node_id,
    }
    assert dead.node_id not in {node.node_id for node in snapshot.eligible_nodes}


def test_decision_validation_rejects_stale_unknown_and_over_capacity(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={}, gate_result=_gate(), depth=0, status="active",
        )
    snapshot = build_group_snapshot(
        store, max_research_per_node=3, max_proposals_per_node=9,
    )

    valid = SupervisorDecision(
        decision_id="decision-1",
        epoch_id=snapshot.epoch_id,
        snapshot_watermark=snapshot.watermark,
        allocations=(AllocationDirective(root.node_id, 2),),
        rationale="fund a distinct branch",
        evidence_refs=(f"node:{root.node_id}",),
    )
    assert validate_decision(snapshot, valid, proposer_capacity=2) == valid

    with pytest.raises(ValueError, match="stale"):
        validate_decision(
            snapshot,
            SupervisorDecision(
                **{**valid.__dict__, "snapshot_watermark": "old"}
            ),
            proposer_capacity=2,
        )
    with pytest.raises(ValueError, match="eligible"):
        validate_decision(
            snapshot,
            SupervisorDecision(
                **{**valid.__dict__, "allocations": (AllocationDirective("missing", 1),)}
            ),
            proposer_capacity=2,
        )
    with pytest.raises(ValueError, match="capacity"):
        validate_decision(snapshot, valid, proposer_capacity=0)


def test_scheduler_uses_supervisor_selection_before_frontier(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=_gate(), depth=0,
            status="active",
        )
        tx.create_episode(node_id=root.node_id)
        dormant = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-old", sha="old",
            metrics={"score": 1}, gate_result=_gate(), depth=1,
            status="dormant",
        )
        tx.create_episode(node_id=dormant.node_id)

    submitted = []

    def decide(snapshot, capacity):
        return SupervisorDecision(
            decision_id="decision-1",
            epoch_id=snapshot.epoch_id,
            snapshot_watermark=snapshot.watermark,
            allocations=(AllocationDirective(dormant.node_id, 1),),
            rationale="protect a distinct low-base branch",
            evidence_refs=(f"node:{dormant.node_id}",),
        )

    scheduler = Scheduler(
        store,
        tmp_path,
        SchedulerConfig(max_proposer_inflight=1, proposal_slots=3),
        submit_proposer=lambda allocation_id, payload: submitted.append(payload),
        supervisor_decider=decide,
    )

    jobs = scheduler._allocate_proposers(Frontier({root.node_id}, {}))

    assert len(jobs) == 1
    assert submitted[0]["node_id"] == dormant.node_id
    assert len(submitted[0]["proposal_ids"]) == 1


def test_scheduler_falls_back_to_frontier_when_supervisor_fails(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={}, gate_result=_gate(), depth=0, status="active",
        )
        tx.create_episode(node_id=root.node_id)
    submitted = []
    scheduler = Scheduler(
        store,
        tmp_path,
        SchedulerConfig(max_proposer_inflight=1, proposal_slots=2),
        submit_proposer=lambda allocation_id, payload: submitted.append(payload),
        supervisor_decider=lambda snapshot, capacity: (_ for _ in ()).throw(
            RuntimeError("supervisor unavailable")
        ),
    )

    jobs = scheduler._allocate_proposers(Frontier({root.node_id}, {}))

    assert len(jobs) == 1
    assert submitted[0]["node_id"] == root.node_id
    assert len(submitted[0]["proposal_ids"]) == 2
