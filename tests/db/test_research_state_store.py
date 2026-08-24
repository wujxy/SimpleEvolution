"""Persistence tests for ResearchState."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.research_state import ResearchState


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def test_research_state_round_trip(store: ResearchStore):
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
        state = tx.create_research_state(
            ResearchState(
                research_state_id="rs-episode-1-001",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                derived_from_research_state_id=None,
                transformation_id=None,
                working_model="The boundary loses reusable state.",
                evidence_refs=("source:src/fcn.cc:FCN",),
                created_at=2.0,
            )
        )

    queries = ResearchQueries(store.path)
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


def _node_and_episode(store: ResearchStore):
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="root-batch",
            metrics={},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
    return node, episode


def _batch_records(node, episode):
    research_state_id = f"rs-{episode.episode_id}-001"
    return (
        [{
            "research_state_id": research_state_id,
            "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "derived_from_research_state_id": None,
            "working_model": "The boundary loses reusable state.",
            "evidence_refs": ["source:src/fcn.cc:FCN"],
            "created_at": 2.0,
        }],
        research_state_id,
    )


def test_publish_research_batch_persists_state_and_two_proposals(store):
    node, episode = _node_and_episode(store)
    states, state_id = _batch_records(node, episode)
    proposals = store.publish_research_batch(
        node_id=node.node_id,
        episode_id=episode.episode_id,
        research_states=states,
        proposals=[
            {
                "proposal_id": "p-1",
                "research_state_id": state_id,
                "instruction": "try X",
                "rationale": {"expectation": "metric improves"},
                "research_operation": "explore",
                "donor_experiment_ids": [],
            },
            {
                "proposal_id": "p-2",
                "research_state_id": state_id,
                "instruction": "try Y",
                "rationale": {"expectation": "memory falls"},
                "research_operation": "explore",
                "donor_experiment_ids": [],
            },
        ],
        reserved_proposal_ids=("p-1", "p-2"),
    )
    queries = ResearchQueries(store.path)
    assert [item.proposal_id for item in proposals] == ["p-1", "p-2"]
    assert queries.get_research_state(state_id) is not None
    assert {item.research_state_id for item in queries.queued_proposals()} == {state_id}
    assert {item.research_operation for item in proposals} == {"explore"}


def test_publish_research_batch_persists_state_only_abstention(store):
    node, episode = _node_and_episode(store)
    states, state_id = _batch_records(node, episode)
    created = store.publish_research_batch(
        node_id=node.node_id,
        episode_id=episode.episode_id,
        research_states=states,
        proposals=[],
        reserved_proposal_ids=(),
    )
    queries = ResearchQueries(store.path)
    assert created == []
    assert queries.get_research_state(state_id) is not None
    assert queries.queued_proposals() == []


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (
            lambda node, episode, states, proposals: states[0].update(
                node_id="node-forged"
            ),
            "research state belongs to another node or episode",
        ),
        (
            lambda node, episode, states, proposals: states.append(
                dict(states[0])
            ),
            "duplicate research_state_id",
        ),
        (
            lambda node, episode, states, proposals: proposals[0].update(
                proposal_id="p-forged"
            ),
            "not in reserved pool",
        ),
        (
            lambda node, episode, states, proposals: proposals[0].update(
                research_state_id="rs-missing"
            ),
            "unknown research_state_id",
        ),
    ],
)
def test_publish_research_batch_rolls_back_invalid_payload(store, mutate, error):
    node, episode = _node_and_episode(store)
    states, state_id = _batch_records(node, episode)
    proposals = [{
        "proposal_id": "p-1",
        "research_state_id": state_id,
        "instruction": "try X",
        "rationale": {"expectation": "metric improves"},
    }]
    mutate(node, episode, states, proposals)
    with pytest.raises(ValueError, match=error):
        store.publish_research_batch(
            node_id=node.node_id,
            episode_id=episode.episode_id,
                research_states=states,
            proposals=proposals,
            reserved_proposal_ids=("p-1",),
        )
    queries = ResearchQueries(store.path)
    assert queries.research_states_for_episode(episode.episode_id) == []
    assert queries.queued_proposals() == []
