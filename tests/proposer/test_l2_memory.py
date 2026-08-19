"""Tests for L2-backed memory service."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from proposer.l2_memory import L2MemoryService
from simpleevo.db.store import GateDecision, GateResult, ResearchStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def test_inspect_experiment(store: ResearchStore):
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
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "p1",
                "node_id": root.node_id,
                "episode_id": episode.episode_id,
                "instruction": "try X",
                "rationale": {},
                "status": "queued",
                "created_at": 0.0,
            })()
        )
        experiment = tx.create_experiment(
            experiment_id="exp-1",
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
        tx.update_experiment_result(
            experiment_id=experiment.experiment_id,
            result_sha="sha-child",
            metrics={"total_ms": 90.0},
            gate_result=GateDecision(
                {"PASS": GateResult(True, "")}, True
            ),
            status="completed",
        )

    mem = L2MemoryService(store.path.parent)
    episode = mem.inspect_experiment("exp-1")
    assert episode["experiment_id"] == "exp-1"
    assert episode["parent_sha"] == "sha-root"
    assert episode["result_sha"] == "sha-child"
    assert episode["metrics"]["total_ms"] == 90.0
    assert episode["gate"]["passed"] is True


def test_search_experiments(store: ResearchStore):
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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "p1",
                "node_id": root.node_id,
                "episode_id": episode.episode_id,
                "instruction": "try X",
                "rationale": {},
                "status": "queued",
                "created_at": 0.0,
            })()
        )
        tx.create_experiment(
            experiment_id="exp-1",
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )

    mem = L2MemoryService(store.path.parent)
    result = mem.search_experiments("", limit=10, buckets=False)
    assert len(result["results"]) == 1
    assert result["results"][0]["experiment_id"] == "exp-1"
