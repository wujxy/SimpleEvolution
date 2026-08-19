"""Tests for the executor queue."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.scheduler.frontier import Frontier
from simpleevo.scheduler.queue import ExecutorQueue, QueueConfig


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def _seed(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
            created_at=1.0,
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
            created_at=1.0,
        )
        tx.create_proposal(
            type("P", (), {
                "proposal_id": "p1",
                "node_id": root.node_id,
                "episode_id": episode.episode_id,
                "instruction": "A",
                "rationale": {},
                "status": "queued",
                "created_at": 1.0,
            })()
        )
        tx.create_proposal(
            type("P", (), {
                "proposal_id": "p2",
                "node_id": root.node_id,
                "episode_id": episode.episode_id,
                "instruction": "B",
                "rationale": {},
                "status": "queued",
                "created_at": 2.0,
            })()
        )
        return root.node_id


def test_enqueue_and_dequeue_fifo(store: ResearchStore):
    node_id = _seed(store)
    queue = ExecutorQueue(store, {node_id}, QueueConfig(max_size=10))

    dequeued = queue.dequeue(2)
    assert dequeued == ["p1", "p2"]


def test_overflow_becomes_dormant(store: ResearchStore):
    node_id = _seed(store)
    queue = ExecutorQueue(store, {node_id}, QueueConfig(max_size=1))

    # Two proposals are queued; max_size=1 keeps the oldest, overflows the newest.
    overflowed = queue.enforce_bound()
    assert overflowed == 1
    queued = queue.dequeue(10)
    assert queued == ["p1"]
    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT status FROM proposals WHERE proposal_id = 'p2'"
        ).fetchone()
        assert row["status"] == "overflowed_dormant"


def test_parent_not_in_frontier_becomes_dormant(store: ResearchStore):
    node_id = _seed(store)
    queue = ExecutorQueue(store, set(), QueueConfig(max_size=10))

    cleaned = queue.cleanup()
    assert cleaned == 2
    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT status FROM proposals WHERE proposal_id = 'p1'"
        ).fetchone()
        assert row["status"] == "dormant"
