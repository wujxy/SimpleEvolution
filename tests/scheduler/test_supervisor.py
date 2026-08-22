import json
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

    allocation = store.get_allocation(jobs[0])
    proposal_id = submitted[0]["proposal_ids"][0]
    state_id = f"rs-{allocation.episode_id}-supervisor-admission"
    store.publish_research_batch(
        node_id=dormant.node_id,
        episode_id=allocation.episode_id,
        transformations=(),
        research_states=({
            "research_state_id": state_id,
            "node_id": dormant.node_id,
            "episode_id": allocation.episode_id,
            "working_model": "The low-base branch contains distinct evidence.",
            "evidence_refs": [f"node:{dormant.node_id}"],
        },),
        proposals=({
            "proposal_id": proposal_id,
            "research_state_id": state_id,
            "instruction": "test the distinct mechanism",
            "rationale": {},
            "research_operation": "explore",
            "donor_experiment_ids": [],
        },),
        reserved_proposal_ids=allocation.reserved_proposal_ids,
    )
    store.deallocate_proposer(
        allocation_id=allocation.allocation_id,
        proposals_produced=1,
    )
    experiments = []
    scheduler.submit_experiment = lambda work_id, payload: experiments.append(
        (work_id, payload)
    )

    experiment_jobs = scheduler._drain_executor_queue()

    assert len(experiment_jobs) == 1
    assert experiments[0][1]["parent_node_id"] == dormant.node_id
    assert experiments[0][1]["parent_sha"] == dormant.sha
    assert store.get_proposal(proposal_id).status == "running"


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


def test_scheduler_submits_and_ingests_supervisor_worker(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=_gate(), depth=0, status="active",
        )
        tx.create_episode(node_id=root.node_id)
        dormant = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-old", sha="old",
            metrics={"score": 1}, gate_result=_gate(), depth=1, status="dormant",
        )
        tx.create_episode(node_id=dormant.node_id)

    class Submitter:
        presumes_dead_on_startup = False

        def __init__(self):
            self.supervisor = []
            self.proposers = []

        def submit_supervisor(self, work_id, payload):
            self.supervisor.append((work_id, payload))
            return str(tmp_path / "supervisor_decisions" / work_id / "result.json")

        def submit_proposer(self, work_id, payload):
            self.proposers.append((work_id, payload))
            return ""

        def submit_experiment(self, work_id, payload):
            return ""

    submitter = Submitter()
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(max_proposer_inflight=1, proposal_slots=3),
        submitter=submitter,
    )
    frontier = Frontier({root.node_id}, {})

    assert scheduler._allocate_proposers(frontier) == []
    work_id, payload = submitter.supervisor[0]
    result_path = tmp_path / "supervisor_decisions" / work_id / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({
        "status": "completed",
        "result": {
            "decision_id": work_id,
            "epoch_id": payload["snapshot"]["epoch_id"],
            "snapshot_watermark": payload["snapshot"]["watermark"],
            "allocations": [{"node_id": dormant.node_id, "proposal_slots": 1}],
            "rationale": "protect diversity",
            "evidence_refs": [f"node:{dormant.node_id}"],
            "integration_request": None,
        },
    }), encoding="utf-8")

    jobs = scheduler._allocate_proposers(frontier)

    assert len(jobs) == 1
    assert submitter.proposers[0][1]["node_id"] == dormant.node_id
    assert len(submitter.proposers[0][1]["proposal_ids"]) == 1


def test_empty_supervisor_decision_is_not_repeated_for_same_snapshot(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root", metrics={},
            gate_result=_gate(), depth=0, status="active",
        )
        tx.create_episode(node_id=root.node_id)

    class Submitter:
        presumes_dead_on_startup = False

        def __init__(self):
            self.calls = []

        def submit_supervisor(self, work_id, payload):
            self.calls.append((work_id, payload))
            return ""

        def submit_proposer(self, work_id, payload):
            raise AssertionError("empty decision must not allocate")

        def submit_experiment(self, work_id, payload):
            return ""

    submitter = Submitter()
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(max_proposer_inflight=1, quiescence_window_proposals=1),
        submitter=submitter,
    )
    frontier = Frontier({root.node_id}, {})
    assert scheduler._allocate_proposers(frontier) == []
    work_id, payload = submitter.calls[0]
    path = tmp_path / "supervisor_decisions" / work_id / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "status": "completed",
        "result": {
            "decision_id": work_id,
            "epoch_id": payload["snapshot"]["epoch_id"],
            "snapshot_watermark": payload["snapshot"]["watermark"],
            "allocations": [],
            "rationale": "no marginal work",
            "evidence_refs": [],
        },
    }), encoding="utf-8")

    assert scheduler._allocate_proposers(frontier) == []
    assert scheduler._allocate_proposers(frontier) == []
    assert len(submitter.calls) == 1
    scheduler._step_count = 1
    assert scheduler._quiescent() is True
