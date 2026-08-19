"""Tests for ResearchStore: ingest, lineage, and atomicity."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, GateResult, ResearchStore


def _gate(passed: bool) -> GateDecision:
    return GateDecision(
        results={"PASS": GateResult(passed, "")},
        passed=passed,
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def test_create_root_node_and_thread(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="abc123",
            metrics={"total_ms": 100.0},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )

    assert root.depth == 0
    assert root.parent_node_id is None

    q = ResearchQueries(store.path)
    assert q.get_node(root.node_id) == root
    assert q.get_thread(thread.thread_id) == thread


def test_ingest_experiment_result_creates_child_node(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="parent",
            metrics={"total_ms": 100.0},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="snap.tgz",
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
                "node_id": root.node_id,
                "thread_id": thread.thread_id,
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
            status="running",
        )

    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="childsha",
        metrics={"total_ms": 90.0},
        gate_result=_gate(True),
        status="completed",
    )

    assert child is not None
    assert child.parent_node_id == root.node_id
    assert child.sha == "childsha"
    assert child.depth == 1
    assert child.experiment_id == experiment.experiment_id

    q = ResearchQueries(store.path)
    exp = q.get_experiment(experiment.experiment_id)
    assert exp is not None
    assert exp.child_node_id == child.node_id
    assert exp.status == "completed"

    # The forked child thread inherits the PARENT thread's final state, not a
    # per-proposal snapshot.
    child_thread = q.get_thread(
        q.threads_for_node(child.node_id)[0].thread_id)
    assert child_thread.snapshot_ref == "snap.tgz"


def test_ingest_gate_failed_does_not_create_node(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="parent",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
                "node_id": root.node_id,
                "thread_id": thread.thread_id,
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
            status="running",
        )

    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="childsha",
        metrics={},
        gate_result=_gate(False),
        status="gate_rejected",
    )

    assert child is None
    q = ResearchQueries(store.path)
    exp = q.get_experiment(experiment.experiment_id)
    assert exp is not None
    assert exp.child_node_id is None
    assert exp.status == "gate_rejected"


def test_publish_proposals(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="parent",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )

    proposals = store.publish_proposals(
        node_id=root.node_id,
        thread_id=thread.thread_id,
        proposals=[
            {
                "proposal_id": "prop-a",
                "instruction": "inline A",
                "rationale": {"why": "reason A"},
            },
            {
                "proposal_id": "prop-b",
                "instruction": "inline B",
                "rationale": {"why": "reason B"},
            },
        ],
        final_snapshot_ref="final.tgz",
    )

    assert len(proposals) == 2
    assert proposals[0].status == "queued"
    assert proposals[0].node_id == root.node_id
    assert proposals[0].proposal_id == "prop-a"

    q = ResearchQueries(store.path)
    assert len(q.queued_proposals()) == 2
    assert q.get_thread(thread.thread_id).snapshot_ref == "final.tgz"


def test_publish_proposals_rejects_id_outside_reserved_pool(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="parent",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )

    with pytest.raises(ValueError, match="not in reserved pool"):
        store.publish_proposals(
            node_id=root.node_id,
            thread_id=thread.thread_id,
            proposals=[
                {
                    "proposal_id": "forged-id",
                    "instruction": "inline A",
                    "rationale": {},
                },
            ],
            reserved_proposal_ids=("prop-a", "prop-b"),
        )


def test_tree_lineage(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="root",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        child = tx.create_node(
            parent_node_id=root.node_id,
            experiment_id="exp-1",
            sha="child",
            metrics={},
            gate_result=_gate(True),
            depth=1,
            status="active",
        )
        grandchild = tx.create_node(
            parent_node_id=child.node_id,
            experiment_id="exp-2",
            sha="grandchild",
            metrics={},
            gate_result=_gate(True),
            depth=2,
            status="active",
        )

    q = ResearchQueries(store.path)
    lineage = q.node_lineage(grandchild.node_id)
    assert [n.sha for n in lineage] == ["root", "child", "grandchild"]

    tree = q.tree()
    assert tree[root.node_id].children == (child.node_id,)
    assert tree[child.node_id].children == (grandchild.node_id,)
    assert tree[grandchild.node_id].children == ()


def test_ingest_idempotency_rejects_double_terminal(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="parent",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
                "node_id": root.node_id,
                "thread_id": thread.thread_id,
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
            status="running",
        )

    store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="childsha",
        metrics={},
        gate_result=_gate(True),
        status="completed",
    )

    with pytest.raises(ValueError, match="already terminal"):
        store.ingest_experiment_result(
            experiment_id=experiment.experiment_id,
            result_sha="childsha2",
            metrics={},
            gate_result=_gate(True),
            status="completed",
        )
