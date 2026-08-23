"""Proposal-specific ResearchStateSeed assembly and rendering."""
from __future__ import annotations

from pathlib import Path

import pytest

from proposer.context import build_generator_catalog, build_research_state_seed_pack
from simpleevo.generator import Generator
from simpleevo.db.store import GateDecision, Proposal, ResearchStore
from simpleevo.research_state import ResearchState
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


@pytest.fixture
def store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path / "simpleevo.db")


def _scheduler(store: ResearchStore) -> Scheduler:
    return Scheduler(
        store,
        store.path.parent,
        SchedulerConfig(
            max_proposer_inflight=0,
            max_experiment_inflight=0,
            poll_seconds=0.0,
        ),
    )


def _seed_root(store: ResearchStore):
    with store.transaction() as tx:
        return tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )


def _seed_completed_research_path(store: ResearchStore):
    root = _seed_root(store)
    with store.transaction() as tx:
        episode = tx.create_episode(node_id=root.node_id)
        state_id = f"rs-{episode.episode_id}-001"
        tx.create_research_state(ResearchState(
            research_state_id=state_id,
            node_id=root.node_id,
            episode_id=episode.episode_id,
            derived_from_research_state_id=None,
            transformation_id=None,
            working_model="The boundary loses reusable state.",
            evidence_refs=("source:src/fcn.cc:FCN",),
            created_at=1.0,
        ))
        tx.create_proposal(Proposal(
            proposal_id="proposal-1",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            instruction="Preserve reusable state across FCN calls.",
            rationale={
                "expectation": "total_ms decreases",
                "material_difference": "Moves ownership across calls.",
            },
            status="running",
            created_at=2.0,
            research_state_id=state_id,
        ))
        experiment = tx.create_experiment(
            experiment_id="experiment-1",
            proposal_id="proposal-1",
            parent_node_id=root.node_id,
        )
    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="sha-child",
        metrics={"total_ms": 90.0},
        gate_result=GateDecision({}, True),
        status="completed",
        changed_paths=("src/fcn.cc",),
    )
    assert child is not None
    return child


def test_child_seed_joins_state_expectation_and_outcome(store):
    child = _seed_completed_research_path(store)
    seed = _scheduler(store)._research_state_seed_for(child)
    assert seed["child_node"]["node_id"] == child.node_id
    assert seed["originating_research_state"]["working_model"] == (
        "The boundary loses reusable state."
    )
    assert seed["proposal"]["expectation"] == "total_ms decreases"
    assert seed["experiment"]["metrics"] == {"total_ms": 90.0}
    assert seed["experiment"]["parent_metrics"] == {"total_ms": 100.0}
    assert "interpretation" not in seed


def test_root_has_no_research_state_seed(store):
    root = _seed_root(store)
    assert _scheduler(store)._research_state_seed_for(root) == {}


def test_seed_pack_gives_child_facts_before_predecessor_note(store):
    seed = _scheduler(store)._research_state_seed_for(
        _seed_completed_research_path(store)
    )
    text = build_research_state_seed_pack(seed)
    assert "You are a newly assigned Scientist" in text
    assert "Current Child Node — authoritative Harness facts" in text
    assert "Experiment outcome — authoritative Harness facts" in text
    assert "Predecessor proposal — prior intervention and expectation" in text
    # The predecessor's note travels first-hand, in full, after the facts
    # (probe-verified: under the anti-anchor charter an inlined note does
    # not collapse the candidate set; the v4 pointer measured 0/10 use).
    # No framing is added — how to treat the note is the successor's own
    # judgment, per its charter.
    assert "Your predecessor left this note:" in text
    assert "The boundary loses reusable state." in text
    assert "deliberately NOT shown" not in text
    assert "Preserve reusable state across FCN calls." in text
    assert text.index("Current Child Node") < text.index("Experiment outcome")
    assert text.index("Experiment outcome") < text.index("Predecessor proposal")
    assert text.index("Predecessor proposal") < text.index(
        "Your predecessor left this note:")


def test_generator_catalog_exposes_all_choices_without_a_recommendation():
    text = build_generator_catalog([
        Generator("G1", "Assumption Attack", "Attack a critical assumption."),
        Generator("G2", "Boundary Shift", "Shift the problem boundary."),
    ])
    assert "G1 — Assumption Attack" in text
    assert "G2 — Boundary Shift" in text
    assert "recommended" not in text.lower()


def test_child_proposer_payload_uses_research_state_seed(store):
    child = _seed_completed_research_path(store)
    scheduler = _scheduler(store)
    episode = scheduler._queries.episodes_for_node(child.node_id)[0]
    allocation = store.allocate_proposer(
        node_id=child.node_id,
        episode_id=episode.episode_id,
        proposal_slots=1,
    )
    payload = scheduler._proposer_payload(
        allocation, child, episode, "attempt-1", 1,
    )
    assert payload["research_state_seed"]["proposal"]["proposal_id"] == (
        "proposal-1"
    )
    assert "world_transition" not in payload
