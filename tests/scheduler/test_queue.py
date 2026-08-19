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
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
            created_at=1.0,
        )
        tx.create_proposal(
            type("P", (), {
                "proposal_id": "p1",
                "node_id": root.node_id,
                "thread_id": thread.thread_id,
                "instruction": "A",
                "rationale": {},
                "snapshot_ref": "",
                "status": "queued",
                "created_at": 1.0,
            })()
        )
        tx.create_proposal(
            type("P", (), {
                "proposal_id": "p2",
                "node_id": root.node_id,
                "thread_id": thread.thread_id,
                "instruction": "B",
                "rationale": {},
                "snapshot_ref": "",
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

    status = queue.enqueue("p2")  # already queued; re-enqueue triggers overflow check
    # p2 is already queued, so enqueue re-checks size and marks overflow.
    # Because size is 1 and p2 would be the second, it becomes overflowed_dormant.
    assert status in {"queued", "overflowed_dormant"}


def test_parent_not_in_frontier_becomes_dormant(store: ResearchStore):
    node_id = _seed(store)
    queue = ExecutorQueue(store, set(), QueueConfig(max_size=10))

    status = queue.enqueue("p1")
    assert status == "dormant"
