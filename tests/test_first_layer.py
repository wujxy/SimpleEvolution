"""First-layer inheritance: fact block + handover, body never crosses
(科学家完整研究制 §2.6 — 继承是重著,不是转发)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.context import build_first_layer_pack
from scientist.wake import build_wake_view, first_layer
from simpleevo.db.lease_writer import upsert_lease_research_state
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.generator import load_generator_basis
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
            parent_node_id=None, experiment_id=None, sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True), depth=0, status="active",
        )


def _seed_delivered_child(store: ResearchStore, *, with_handover=True):
    """A delivered world that passed adjudication, with graduated evidence."""
    root = _seed_root(store)
    with store.transaction() as tx:
        episode = tx.create_episode(
            node_id=root.node_id, variation_operator="lens-x",
        )
    allocation = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id, lens="lens-x",
    )
    upsert_lease_research_state(
        store.path, lease_id=allocation.allocation_id,
        episode_id=episode.episode_id, node_id=root.node_id,
        working_model="The boundary loses reusable state. (signed belief)",
        evidence=[
            {"claim": "bucket index halves lookups",
             "how": "self-run bench", "numbers": {"total_ms": 50.0},
             "source": "experiment", "status": "belief", "sha": "c1" * 20},
            {"claim": "an unrelated belief about dead ends",
             "status": "belief"},
        ],
    )
    handover = {
        "dead_ends": ["cache axis: all layouts measured slower"],
        "open_questions": ["prefetch depth"],
        "warning": "the flat profile misleads",
    } if with_handover else None
    ingest = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "c1" * 20, "handover": handover,
        },
    )
    # Adjudication pass: child node + graduation of the delivered evidence.
    child = store.ingest_experiment_result(
        experiment_id=ingest.experiment_id,
        result_sha="c1" * 20,
        metrics={"total_ms": 50.0},
        gate_result=GateDecision({}, True),
        status="completed",
        changed_paths=("src/fcn.cc",),
    )
    store.graduate_delivered_evidence(
        episode_id=episode.episode_id, world_sha="c1" * 20)
    store.conclude_lease(
        allocation_id=allocation.allocation_id, outcome="delivered",
        world_sha="c1" * 20,
    )
    assert child is not None
    return child


def test_first_layer_pushes_facts_and_handover_only(store):
    child = _seed_delivered_child(store)
    layer = first_layer(ResearchQueries(store.path), child)
    assert layer["child_node"]["node_id"] == child.node_id
    assert layer["adjudication"]["metrics"] == {"total_ms": 50.0}
    # The only pushed prose is the handover map.
    assert layer["handover"]["warning"] == "the flat profile misleads"
    # Graduated evidence travels unsigned: status stripped by graduation.
    claims = {e["claim"] for e in layer["graduated_evidence"]}
    assert claims == {"bucket index halves lookups"}
    for entry in layer["graduated_evidence"]:
        assert "status" not in entry
    # The predecessor's BODY is not pushed — only pull ids.
    assert "originating_research_state" not in layer
    assert layer["pull"]["research_state_id"].startswith("rs-")
    assert layer["pull"]["author_lens"] == "lens-x"


def test_root_has_no_first_layer(store):
    root = _seed_root(store)
    assert first_layer(ResearchQueries(store.path), root) == {}


def test_first_layer_pack_orders_facts_before_handover(store):
    child = _seed_delivered_child(store)
    text = build_first_layer_pack(first_layer(
        ResearchQueries(store.path), child))
    assert "You are newly assigned to this Child world" in text
    assert text.index("Current Child Node") < text.index("Adjudication")
    assert text.index("Adjudication") < text.index("Graduated evidence")
    assert text.index("Graduated evidence") < text.index("Handover")
    # The signed belief text never appears in the pack.
    assert "signed belief" not in text
    # The handover's warning does (it is the one pushed prose).
    assert "the flat profile misleads" in text
    # Pull ids are pointers, not content.
    assert "Pull channel" in text


def test_child_proposer_wake_uses_first_layer(store):
    child = _seed_delivered_child(store)
    scheduler = _scheduler(store)
    episode = scheduler._queries.episodes_for_node(child.node_id)[0]
    allocation = store.allocate_proposer(
        node_id=child.node_id, episode_id=episode.episode_id,
        proposal_slots=1,
    )
    payload = scheduler._proposer_payload(
        allocation, child, episode, "attempt-1", 1,
    )
    # Envelope: IDs only.  The first layer is the worker's wake product.
    assert "first_layer" not in payload
    assert "world_transition" not in payload
    view = build_wake_view(
        ResearchQueries(store.path), load_generator_basis(),
        node_id=child.node_id, episode_id=episode.episode_id,
    )
    assert view["first_layer"]["handover"]["warning"] == (
        "the flat profile misleads")
    assert "world_transition" not in view
