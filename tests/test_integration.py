"""Integration test: Scheduler drives proposer → experiment → new node."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig
from simpleevo.scheduler.frontier import Frontier
from proposer.supervisor import (
    AllocationDirective, SupervisorDecision, build_group_snapshot,
)


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        yield run_dir, store


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_scheduler_closes_proposer_experiment_loop(env):
    run_dir, store = env

    # Seed a root node and episode.
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
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    config = SchedulerConfig(
        max_proposer_inflight=1,
        max_experiment_inflight=1,
        frontier=FrontierConfig(axes=("total_ms",)),
        poll_seconds=0.0,
    )

    def submit_proposer(allocation_id: str, payload: dict) -> None:
        state_id = f"rs-{episode.episode_id}-001"
        transformation_id = f"ct-{episode.episode_id}-001"
        _write_json(
            run_dir / "proposer_allocations" / allocation_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "proposer",
                "request_id": allocation_id,
                "status": "completed",
                "result": {
                    "episode_id": episode.episode_id,
                    "node_id": root.node_id,
                    "outcome": "submit",
                    "transformations": [{
                        "transformation_id": transformation_id,
                        "node_id": root.node_id,
                        "episode_id": episode.episode_id,
                        "source_research_state_id": None,
                        "operator_id": "G2",
                        "challenge": "Question the call boundary.",
                        "created_at": 0.5,
                    }],
                    "research_states": [{
                        "research_state_id": state_id,
                        "node_id": root.node_id,
                        "episode_id": episode.episode_id,
                        "derived_from_research_state_id": None,
                        "transformation_id": transformation_id,
                        "working_model": "Repeated setup crosses the call boundary.",
                        "evidence_refs": ["source:src/fcn.cc:FCN"],
                        "created_at": 1.0,
                    }],
                    "proposals": [
                        {
                            "proposal_id": payload["proposal_ids"][0],
                            "research_state_id": state_id,
                            "instruction": "inline a small helper to reduce total_ms",
                            "rationale": {"expectation": "total_ms decreases"},
                        },
                        {
                            "proposal_id": payload["proposal_ids"][1],
                            "research_state_id": state_id,
                            "instruction": "cache the invariant at call scope",
                            "rationale": {
                                "expectation": "total_ms decreases differently",
                                "material_difference": "Tests caching, not ownership.",
                            },
                        },
                    ],
                },
            },
        )

    experiment_results: dict[str, dict] = {}

    def submit_experiment(experiment_id: str, payload: dict) -> None:
        _write_json(
            run_dir / "experiments" / experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment_id,
                "status": "completed",
                "result": {
                    "experiment_id": experiment_id,
                    "child_node_id": None,
                    "parent_sha": "sha-root",
                    "sha": "sha-child",
                    "metrics": {"total_ms": 90.0},
                    "gate": {
                        "passed": True,
                        "results": {"PATHS": {"passed": True, "detail": ""}},
                    },
                    "outcome": "COMPLETED",
                    "eval_block": "TOTAL_MS=90.0\nPATHS=pass",
                    "changed_paths": [],
                },
            },
        )
        experiment_results[experiment_id] = payload

    scheduler = Scheduler(
        store, run_dir, config,
        submit_proposer=submit_proposer,
        submit_experiment=submit_experiment,
    )

    # Step 1: allocate proposer, poll result, publish proposals, drain queue,
    # and ingest the synchronous experiment result.
    t1 = scheduler.step()
    assert t1["proposer_jobs"] == 1
    assert t1["published"] == 1
    assert t1["experiment_jobs"] == 1

    # Verify child node was created.
    with store.transaction() as tx:
        children = tx._conn.execute(
            "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
        ).fetchall()
    assert len(children) == 1
    assert children[0]["sha"] == "sha-child"
    assert children[0]["metrics"] == '{"total_ms": 90.0}'
    queries = ResearchQueries(store.path)
    states = queries.research_states_for_episode(episode.episode_id)
    assert len(states) == 1
    transformations = queries.transformations_for_episode(episode.episode_id)
    assert len(transformations) == 1
    proposals = [
        proposal for proposal in queries.queued_proposals()
        if proposal.node_id == root.node_id
    ]
    experiment_proposal = queries.get_proposal(
        next(iter(experiment_results.values()))["proposal_id"]
    )
    assert experiment_proposal.research_state_id == states[0].research_state_id
    assert len(proposals) == 1
    assert proposals[0].research_state_id == states[0].research_state_id
    seed = scheduler._research_state_seed_for(
        queries.get_node(children[0]["node_id"])
    )
    assert seed["originating_research_state"]["working_model"] == (
        "Repeated setup crosses the call boundary."
    )
    assert seed["experiment"]["metrics"] == {"total_ms": 90.0}


def test_group_workflow_allocates_divergent_branch_and_promotes_shared_epoch(env):
    run_dir, store = env
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=GateDecision({}, True),
            depth=0, status="active",
        )
        root_episode = tx.create_episode(node_id=root.node_id)
        donor_proposal = tx.create_proposal(Proposal(
            proposal_id="donor-proposal", node_id=root.node_id,
            episode_id=root_episode.episode_id, instruction="independent win",
            rationale={}, status="running", created_at=1,
        ))
        donor_experiment = tx.create_experiment(
            experiment_id="donor-experiment",
            proposal_id=donor_proposal.proposal_id,
            parent_node_id=root.node_id, status="running",
        )
    divergent = store.ingest_experiment_result(
        experiment_id=donor_experiment.experiment_id,
        result_sha="divergent", metrics={"score": 1},
        gate_result=GateDecision({}, True), status="completed",
    )
    with store.transaction() as tx:
        tx._conn.execute(
            "UPDATE nodes SET status = 'dormant' WHERE node_id = ?",
            (divergent.node_id,),
        )

    proposer_jobs = []

    def decide(snapshot, capacity):
        return SupervisorDecision(
            decision_id="decision-1", epoch_id=snapshot.epoch_id,
            snapshot_watermark=snapshot.watermark,
            allocations=(AllocationDirective(divergent.node_id, 1),),
            rationale="fund the distinct low-base lineage",
            evidence_refs=(f"experiment:{donor_experiment.experiment_id}",),
            integration_request={
                "integration_request_id": "request-1",
                "target_node_id": root.node_id,
                "donor_experiment_ids": [donor_experiment.experiment_id],
                "selection_rationale": "turn the mature branch into a shared base",
            },
        )

    scheduler = Scheduler(
        store, run_dir,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=1),
        submit_proposer=lambda work_id, payload: proposer_jobs.append(payload),
        supervisor_decider=decide,
    )
    scheduler._allocate_proposers(Frontier({root.node_id}, {}))
    assert proposer_jobs[0]["node_id"] == divergent.node_id

    integrator_payloads = []
    scheduler.submit_integrator = lambda work_id, payload: integrator_payloads.append(payload)
    assert scheduler._schedule_integrators() == ["request-1"]
    payload = integrator_payloads[0]
    state_id = f"rs-{payload['episode_id']}-integration"
    _write_json(run_dir / "integration_requests/request-1/result.json", {
        "status": "completed",
        "result": {
            "outcome": "submitted", "reason": None,
            "research_state": {
                "research_state_id": state_id, "node_id": root.node_id,
                "episode_id": payload["episode_id"],
                "working_model": "the donor can become the common trunk",
                "evidence_refs": ["experiment:donor-experiment"],
            },
            "proposal": {
                "proposal_id": payload["proposal_id"],
                "research_state_id": state_id,
                "instruction": "port the validated donor onto root",
                "rationale": {}, "research_operation": "synthesize",
                "donor_experiment_ids": ["donor-experiment"],
                "evidence_refs": ["experiment:donor-experiment"],
            },
        },
    })
    assert scheduler._poll_integrators() == ["request-1"]

    def execute(experiment_id, payload):
        _write_json(run_dir / "experiments" / experiment_id / "result.json", {
            "status": "completed",
            "result": {
                "outcome": "COMPLETED", "sha": "shared-candidate",
                "metrics": {"score": 12},
                "gate": {"passed": True, "results": {}},
                "changed_paths": [],
            },
        })

    scheduler.submit_experiment = execute
    jobs = scheduler._drain_executor_queue(Frontier({root.node_id}, {}))
    assert scheduler._poll_experiments() == jobs
    scheduler._apply_epoch_review({
        "integration_request_id": "request-1", "action": "promote",
        "rationale": "candidate passed ordinary evaluation",
        "evidence_refs": [f"experiment:{jobs[0]}"],
    })

    assert store.current_epoch().root_node_id != root.node_id
    snapshot = build_group_snapshot(
        store, max_research_per_node=3, max_proposals_per_node=9,
    )
    assert root.node_id in {item.node_id for item in snapshot.eligible_nodes}
