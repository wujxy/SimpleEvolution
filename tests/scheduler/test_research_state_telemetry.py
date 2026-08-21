"""Deterministic Node-local Research State width telemetry."""
from __future__ import annotations

import json

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, Proposal, ResearchStore
from simpleevo.research_state import ResearchState
from simpleevo.scheduler.telemetry import TelemetryRecorder


def test_research_state_width_counts_identity_links(tmp_path):
    store = ResearchStore(tmp_path / "simpleevo.db")
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
        state_ids = [f"rs-{episode.episode_id}-{index:03d}" for index in range(1, 4)]
        for index, state_id in enumerate(state_ids):
            tx.create_research_state(ResearchState(
                research_state_id=state_id,
                node_id=node.node_id,
                episode_id=episode.episode_id,
                derived_from_research_state_id=None,
                transformation_id=None,
                working_model=f"Working model {index}",
                evidence_refs=(),
                created_at=float(index),
            ))
        for index, state_id in enumerate(
            [state_ids[0], state_ids[0], state_ids[0], state_ids[1], state_ids[2]],
            start=1,
        ):
            tx.create_proposal(Proposal(
                proposal_id=f"proposal-{index}",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                instruction=f"try {index}",
                rationale={},
                status="queued",
                created_at=float(index),
                research_state_id=state_id,
            ))

    queries = ResearchQueries(store.path)
    recorder = TelemetryRecorder(tmp_path)
    record = recorder.record(step=4, frontier_size=1, queries=queries)
    assert record.research_state_width[0] == {
        "node_id": node.node_id,
        "registered_states": 3,
        "proposed_states": 3,
        "total_proposals": 5,
        "max_proposals_per_state": 3,
    }
    lines = (
        tmp_path / "telemetry" / "research_state_width.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["max_proposals_per_state"] == 3
