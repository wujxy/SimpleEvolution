from pathlib import Path

import pytest

from simpleevo.memory.l2 import L2MemoryService
from supervisor.agent import SupervisorTools
from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _gate(passed=True):
    return GateDecision({"PASS": GateResult(passed, "")}, passed)


def _candidate(store: ResearchStore, *, outcome="completed", passed=True):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root", metrics={},
            gate_result=_gate(), depth=0, status="active",
        )
        episode = tx.create_episode(node_id=root.node_id)
        proposal = tx.create_proposal(Proposal(
            proposal_id="integration-proposal", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="combine",
            rationale={}, status="running", created_at=1,
            research_operation="synthesize", donor_experiment_ids=("donor",),
        ))
        experiment = tx.create_experiment(
            experiment_id="integration-exp", proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id, status="running",
        )
    epoch = store.current_epoch()
    store.create_integration_request(
        integration_request_id="req-1", epoch_id=epoch.epoch_id,
        target_node_id=root.node_id, donor_experiment_ids=("donor",),
        selection_rationale="combine",
    )
    store.finish_integration_request(
        "req-1", status="submitted", proposal_id=proposal.proposal_id,
        experiment_id=experiment.experiment_id,
    )
    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="candidate", metrics={"score": 3},
        gate_result=_gate(passed), status=outcome,
    )
    return root, child


def test_gate_rejected_integration_closes_without_epoch(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _candidate(store, outcome="gate_rejected", passed=False)
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
    )

    scheduler._resolve_integration_outcomes()

    assert store.current_epoch().epoch_id == "epoch-0"
    assert store.get_integration_request("req-1").status == "closed"


def _commit_epoch_review(store: ResearchStore, review: dict) -> None:
    """Drive the production path: review applied inside the decision tx."""
    store.commit_supervisor_decision(
        decision_id=f"d-{review['action']}", work_id="supervisor-review",
        decision_kind="epoch_review", rationale=review["rationale"],
        detail=dict(review), cursor_to=store.supervisor_event_head(),
        epoch_review=review,
    )


def test_supervisor_review_promotes_candidate_without_rewriting_history(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, child = _candidate(store)

    _commit_epoch_review(store, {
        "integration_request_id": "req-1",
        "action": "promote",
        "rationale": "combined candidate passed the gate",
        "evidence_refs": ["experiment:integration-exp"],
    })

    epoch = store.current_epoch()
    assert epoch.previous_epoch_id == "epoch-0"
    assert epoch.root_node_id == child.node_id
    assert store.get_integration_request("req-1").status == "promoted"
    # The old epoch root remains a selectable world after promotion.
    listing = SupervisorTools(
        L2MemoryService(tmp_path, db_path=store.path),
        runtime_facts={
            "max_research_per_node": 3,
            "max_proposals_per_node": 9,
        },
    ).execute({"action": "list_nodes"})
    by_id = {row["node_id"]: row for row in listing["nodes"]}
    assert by_id[root.node_id]["allocatable"] is True


def test_cannot_promote_unvalidated_candidate(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _candidate(store, outcome="gate_rejected", passed=False)

    with pytest.raises(ValueError, match="gate-passed"):
        _commit_epoch_review(store, {
            "integration_request_id": "req-1", "action": "promote",
            "rationale": "ignore gate", "evidence_refs": [],
        })
