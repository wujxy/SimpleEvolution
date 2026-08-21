"""Persistence tests for ResearchState and CognitiveTransformation."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.research_state import CognitiveTransformation, ResearchState


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def test_research_state_and_transformation_round_trip(store: ResearchStore):
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="root",
            metrics={},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
        transformation = tx.create_cognitive_transformation(
            CognitiveTransformation(
                transformation_id="ct-episode-1-001",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                source_research_state_id=None,
                operator_id="G2",
                challenge="Question the current component boundary.",
                created_at=1.0,
            )
        )
        state = tx.create_research_state(
            ResearchState(
                research_state_id="rs-episode-1-001",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                derived_from_research_state_id=None,
                transformation_id=transformation.transformation_id,
                working_model="The boundary loses reusable state.",
                evidence_refs=("source:src/fcn.cc:FCN",),
                created_at=2.0,
            )
        )

    queries = ResearchQueries(store.path)
    assert queries.get_transformation(transformation.transformation_id) == transformation
    assert queries.get_research_state(state.research_state_id) == state
    assert queries.research_states_for_episode(episode.episode_id) == [state]


def test_research_state_json_conversion_preserves_evidence_refs():
    state = ResearchState(
        research_state_id="rs-ep-1-001",
        node_id="node-1",
        episode_id="ep-1",
        derived_from_research_state_id=None,
        transformation_id=None,
        working_model="Repeated work crosses the FCN boundary.",
        evidence_refs=("source:src/fcn.cc:FCN",),
        created_at=1.0,
    )

    from simpleevo.research_state import research_state_to_dict

    assert research_state_to_dict(state)["evidence_refs"] == [
        "source:src/fcn.cc:FCN"
    ]
