"""Integration test: Scheduler drives proposer → experiment → new node."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


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
