"""Tests for L2-backed memory service."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.memory.l2 import L2MemoryService
from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.research_state import ResearchState


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def _seed_sibling_experiments(store: ResearchStore) -> dict[str, str]:
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
        state = tx.create_research_state(ResearchState(
            research_state_id="rs-source-001",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            derived_from_research_state_id=None,
            transformation_id=None,
            working_model="Lookup traversal and layout may be coupled.",
            evidence_refs=("source:src/lookup.c:lookup",),
            created_at=1.0,
        ))
        proposal_a = tx.create_proposal(type("P", (), {
            "proposal_id": "p-layout",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": state.research_state_id,
            "instruction": "change lookup layout from AoS to SoA",
            "rationale": {"expectation": "total_ms decreases"},
            "status": "queued",
            "created_at": 2.0,
        })())
        proposal_b = tx.create_proposal(type("P", (), {
            "proposal_id": "p-cache",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": state.research_state_id,
            "instruction": "cache repeated lookup coefficients",
            "rationale": {"expectation": "total_ms decreases"},
            "status": "queued",
            "created_at": 3.0,
        })())
        exp_a = tx.create_experiment(
            experiment_id="exp-layout",
            proposal_id=proposal_a.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
        tx.update_experiment_result(
            experiment_id=exp_a.experiment_id,
            result_sha="sha-layout",
            metrics={"total_ms": 80.0},
            gate_result=GateDecision(
                {"CORRECT": GateResult(True, "")}, True,
            ),
            status="completed",
            changed_paths=("src/layout.c",),
        )
        child = tx.create_node(
            parent_node_id=root.node_id,
            experiment_id=exp_a.experiment_id,
            sha="sha-layout",
            metrics={"total_ms": 80.0},
            gate_result=GateDecision({}, True),
            depth=1,
            status="active",
        )
        tx.link_experiment_child(exp_a.experiment_id, child.node_id)
        exp_b = tx.create_experiment(
            experiment_id="exp-cache",
            proposal_id=proposal_b.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
        tx.update_experiment_result(
            experiment_id=exp_b.experiment_id,
            result_sha="sha-cache",
            metrics={"total_ms": 95.0},
            gate_result=GateDecision(
                {"CORRECT": GateResult(False, "mismatch")}, False,
            ),
            status="gate_rejected",
            changed_paths=("src/cache.c",),
        )
    return {
        "root_node_id": root.node_id,
        "episode_id": episode.episode_id,
        "research_state_id": state.research_state_id,
    }


def test_inspect_experiment_is_world_scoped(store: ResearchStore):
    ids = _seed_sibling_experiments(store)
    detail = L2MemoryService(store.path.parent).inspect_experiment("exp-layout")
    assert detail["experiment_id"] == "exp-layout"
    assert detail["source_world"] == {
        "node_id": ids["root_node_id"],
        "sha": "sha-root",
        "metrics": {"total_ms": 100.0},
    }
    assert detail["intervention"] == {
        "proposal_id": "p-layout",
        "instruction": "change lookup layout from AoS to SoA",
        "changed_paths": ["src/layout.c"],
    }
    assert detail["condition"] == {"recorded_gates": ["CORRECT"]}
    assert detail["observation"]["metrics"] == {"total_ms": 80.0}
    assert detail["observation"]["gate"]["passed"] is True


def test_search_experiments_is_global_but_returns_no_direction_text(
    store: ResearchStore,
):
    _seed_sibling_experiments(store)
    result = L2MemoryService(store.path.parent).search_experiments(
        "lookup", limit=10, buckets=False,
    )
    assert {row["experiment_id"] for row in result["results"]} == {
        "exp-layout", "exp-cache",
    }
    assert all("instruction" not in row for row in result["results"])
    assert all("working_model" not in row for row in result["results"])


def test_coverage_pack_aggregates_regions_without_recommendation(
    store: ResearchStore,
):
    _seed_sibling_experiments(store)
    text = L2MemoryService(store.path.parent).build_coverage_pack()
    assert "src/layout.c: experiments=1 gate_passed=1 gate_failed=0" in text
    assert "src/cache.c: experiments=1 gate_passed=0 gate_failed=1" in text
    assert "promising" not in text.lower()
    assert "change lookup layout" not in text


def test_inspect_originating_research_state_is_attributed(store: ResearchStore):
    ids = _seed_sibling_experiments(store)
    memo = L2MemoryService(
        store.path.parent,
    ).inspect_originating_research_state("exp-layout")
    assert memo["kind"] == "SUBJECTIVE_RESEARCH_MEMO"
    assert memo["research_state_id"] == ids["research_state_id"]
    assert memo["source_episode_id"] == ids["episode_id"]
    assert memo["source_world"] == {
        "node_id": ids["root_node_id"], "sha": "sha-root",
    }
    assert memo["working_model"] == "Lookup traversal and layout may be coupled."


def test_inspect_originating_research_state_reports_unavailable_without_state(
    store: ResearchStore,
):
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
        proposal = tx.create_proposal(type("P", (), {
            "proposal_id": "p-legacy",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": None,
            "instruction": "legacy experiment",
            "rationale": {},
            "status": "queued",
            "created_at": 1.0,
        })())
        tx.create_experiment(
            experiment_id="exp-legacy",
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
    result = L2MemoryService(
        store.path.parent,
    ).inspect_originating_research_state("exp-legacy")
    assert result == {
        "ok": False,
        "error": "research memo unavailable for experiment: exp-legacy",
    }


def test_search_experiments(store: ResearchStore):
    _seed_sibling_experiments(store)
    result = L2MemoryService(store.path.parent).search_experiments(
        "", limit=10, buckets=False,
    )
    assert len(result["results"]) == 2


def test_bucketed_search_surfaces_contrasting_and_path_diverse_evidence(
    store: ResearchStore,
):
    ids = _seed_sibling_experiments(store)
    result = L2MemoryService(store.path.parent).search_experiments(
        "lookup", limit=10, buckets=True,
    )

    all_rows = [
        *result["relevant"],
        *result["contrasting"],
        *result["diverse"],
    ]
    assert {row["experiment_id"] for row in result["relevant"]} == {
        "exp-layout", "exp-cache",
    }
    assert result["contrasting"]
    assert {row["experiment_id"] for row in result["diverse"]} == {
        "exp-layout", "exp-cache",
    }
    assert all(row["source_world"] == {
        "node_id": ids["root_node_id"], "sha": "sha-root",
    } for row in all_rows)
    assert all("instruction" not in row for row in all_rows)


def test_coverage_pack_exposes_neutral_evidence_locator(
    store: ResearchStore,
):
    ids = _seed_sibling_experiments(store)
    text = L2MemoryService(store.path.parent).build_coverage_pack()
    assert f"examples=exp-layout@{ids['root_node_id']}" in text
    assert f"examples=exp-cache@{ids['root_node_id']}" in text
