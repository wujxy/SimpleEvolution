import json
from pathlib import Path

import pytest

from proposer.supervisor import validate_integration_request
from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig
from simpleevo.scheduler.frontier import Frontier


def _gate(passed=True):
    return GateDecision({"PASS": GateResult(passed, "")}, passed)


def _seed(store: ResearchStore, *, donor_passed=True):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root", metrics={},
            gate_result=_gate(), depth=0, status="active",
        )
        episode = tx.create_episode(node_id=root.node_id)
        proposal = tx.create_proposal(Proposal(
            proposal_id="donor-proposal", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="donor change",
            rationale={}, status="queued", created_at=1,
        ))
        experiment = tx.create_experiment(
            experiment_id="donor-exp", proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id, status="running",
        )
        tx.transition_proposal_status(proposal.proposal_id, "running")
    store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="donor-sha", metrics={"score": 2},
        gate_result=_gate(donor_passed),
        status="completed" if donor_passed else "gate_rejected",
    )
    return root


def test_integration_request_requires_gate_passed_donor(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root = _seed(store, donor_passed=False)
    epoch = store.current_epoch()

    with pytest.raises(ValueError, match="gate-passed"):
        validate_integration_request(store, epoch.epoch_id, {
            "integration_request_id": "req-1",
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["donor-exp"],
            "selection_rationale": "combine mature work",
        })


def test_integrator_result_publishes_normal_synthesis_proposal(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root = _seed(store)
    epoch = store.current_epoch()
    store.create_integration_request(
        integration_request_id="req-1", epoch_id=epoch.epoch_id,
        target_node_id=root.node_id, donor_experiment_ids=("donor-exp",),
        selection_rationale="combine mature work",
    )

    class Submitter:
        presumes_dead_on_startup = False

        def __init__(self):
            self.integrators = []

        def submit_integrator(self, work_id, payload):
            self.integrators.append((work_id, payload))
            return ""

        def submit_proposer(self, work_id, payload): return ""
        def submit_experiment(self, work_id, payload): return ""

    submitter = Submitter()
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(max_proposer_inflight=0, max_experiment_inflight=0),
        submitter=submitter,
    )
    assert scheduler._schedule_integrators() == ["req-1"]
    _, payload = submitter.integrators[0]
    path = tmp_path / "integration_requests" / "req-1" / "result.json"
    path.parent.mkdir(parents=True)
    state_id = f"rs-{payload['episode_id']}-integration"
    path.write_text(json.dumps({
        "status": "completed",
        "result": {
            "outcome": "submitted",
            "reason": None,
            "research_state": {
                "research_state_id": state_id,
                "node_id": root.node_id,
                "episode_id": payload["episode_id"],
                "working_model": "independent mechanisms",
                "evidence_refs": ["experiment:donor-exp"],
            },
            "proposal": {
                "proposal_id": payload["proposal_id"],
                "research_state_id": state_id,
                "instruction": "port the donor mechanism",
                "rationale": {},
                "research_operation": "synthesize",
                "donor_experiment_ids": ["donor-exp"],
                "evidence_refs": ["experiment:donor-exp"],
            },
        },
    }), encoding="utf-8")

    assert scheduler._poll_integrators() == ["req-1"]
    request = store.get_integration_request("req-1")
    assert request.status == "submitted"
    proposal = store.get_proposal(request.proposal_id)
    assert proposal.status == "queued"
    assert proposal.research_operation == "synthesize"
    assert proposal.donor_experiment_ids == ("donor-exp",)

    scheduler.config = SchedulerConfig(
        max_proposer_inflight=0, max_experiment_inflight=1,
    )
    submitted = []
    scheduler.submit_experiment = lambda work_id, job: submitted.append((work_id, job))

    jobs = scheduler._drain_executor_queue(Frontier(set(), {}))

    assert len(jobs) == 1
    request = store.get_integration_request("req-1")
    assert request.experiment_id == jobs[0]
    assert submitted[0][1]["parent_node_id"] == root.node_id
