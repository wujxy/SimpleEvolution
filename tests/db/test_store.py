"""Tests for ResearchStore: ingest, lineage, and atomicity."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import (
    GateDecision,
    GateResult,
    LeaseSpec,
    ResearchStore,
    StaleSupervisorDecision,
)


def _gate(passed: bool) -> GateDecision:
    return GateDecision(
        results={"PASS": GateResult(passed, "")},
        passed=passed,
    )


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield ResearchStore(Path(tmp) / "simpleevo.db")


def test_set_node_metrics_records_baseline(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="abc123",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
    assert ResearchQueries(store.path).root_node().metrics == {}
    store.set_node_metrics(root.node_id, {"total_ms": 42.0, "CORRECTNESS": True})
    assert ResearchQueries(store.path).root_node().metrics == {
        "total_ms": 42.0,
        "CORRECTNESS": True,
    }


def test_create_root_node_and_episode(store: ResearchStore):
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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    assert root.depth == 0
    assert root.parent_node_id is None

    q = ResearchQueries(store.path)
    assert q.get_node(root.node_id) == root
    assert q.get_episode(episode.episode_id) == episode


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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
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

    # The forked child episode inherits the PARENT episode's final cognition via
    # the inheritance link, not a per-proposal snapshot.
    child_episode = q.get_episode(
        q.episodes_for_node(child.node_id)[0].episode_id)
    assert child_episode.inherited_from_episode_id == episode.episode_id


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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    proposals = store.publish_proposals(
        node_id=root.node_id,
        episode_id=episode.episode_id,
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
    )

    assert len(proposals) == 2
    assert proposals[0].status == "queued"
    assert proposals[0].node_id == root.node_id
    assert proposals[0].proposal_id == "prop-a"

    q = ResearchQueries(store.path)
    assert len(q.queued_proposals()) == 2


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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    with pytest.raises(ValueError, match="not in reserved pool"):
        store.publish_proposals(
            node_id=root.node_id,
            episode_id=episode.episode_id,
            proposals=[
                {
                    "proposal_id": "forged-id",
                    "instruction": "inline A",
                    "rationale": {},
                },
            ],
            reserved_proposal_ids=("prop-a", "prop-b"),
        )


def test_root_creates_epoch_zero(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="epoch-root",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )

    epoch = store.current_epoch()

    assert epoch is not None
    assert epoch.epoch_id == "epoch-0"
    assert epoch.root_node_id == root.node_id
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
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        proposal = tx.create_proposal(
            type("P", (), {
                "proposal_id": "prop-1",
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

def test_proposal_operations_require_correct_donor_provenance(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="operation-root",
            metrics={}, gate_result=_gate(True), depth=0, status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id,
        )

    with pytest.raises(ValueError, match="explore.*donor"):
        store.publish_proposals(
            node_id=root.node_id, episode_id=episode.episode_id,
            proposals=[{
                "proposal_id": "explore-with-donor",
                "instruction": "new mechanism",
                "research_operation": "explore",
                "donor_experiment_ids": ["exp-donor"],
            }],
        )

    proposal = store.publish_proposals(
        node_id=root.node_id, episode_id=episode.episode_id,
        proposals=[{
            "proposal_id": "synthesis-ok",
            "instruction": "port result",
            "research_operation": "synthesize",
            "donor_experiment_ids": ["exp-donor"],
        }],
    )[0]

    assert proposal.research_operation == "synthesize"
    assert proposal.donor_experiment_ids == ("exp-donor",)

    with pytest.raises(ValueError, match="synthesize.*donor"):
        store.publish_proposals(
            node_id=root.node_id, episode_id=episode.episode_id,
            proposals=[{
                "proposal_id": "synthesis-without-donor",
                "instruction": "port result",
                "research_operation": "synthesize",
            }],
        )


def test_integration_request_is_durable_and_idempotent(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="integration-root",
            metrics={},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )

    epoch = store.current_epoch()
    assert epoch is not None

    request = store.create_integration_request(
        integration_request_id="request-1",
        epoch_id=epoch.epoch_id,
        target_node_id=root.node_id,
        donor_experiment_ids=("donor-a", "donor-b"),
        selection_rationale="complementary validated results",
    )

    same_request = store.create_integration_request(
        integration_request_id="request-1",
        epoch_id=epoch.epoch_id,
        target_node_id=root.node_id,
        donor_experiment_ids=("donor-a", "donor-b"),
        selection_rationale="complementary validated results",
    )

    assert request == same_request
    assert request.status == "open"
    assert request.donor_experiment_ids == ("donor-a", "donor-b")
    assert store.get_integration_request("request-1") == request


# ---------------------------------------------------------------------------
# Supervisor wake events and growth decisions (tree-growth design §4/§9)
# ---------------------------------------------------------------------------


def _seed_root_episode(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sup-root",
            metrics={"total_ms": 1.0},
            gate_result=_gate(True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
    return root, episode


def test_supervisor_events_are_ordered_and_durable(store: ResearchStore):
    first = store.emit_supervisor_event("root_ready", {"root_node_id": "n1"})
    second = store.emit_supervisor_event(
        "experiment_terminal",
        {"experiment_id": "e1", "status": "completed"},
    )
    assert second > first
    assert store.supervisor_event_head() == second
    assert store.supervisor_event_cursor() == 0

    pending = store.pending_supervisor_events()
    assert [item.event_id for item in pending] == [first, second]
    assert pending[1].type == "experiment_terminal"
    assert pending[1].payload["experiment_id"] == "e1"

    reopened = ResearchStore(store.path)
    assert reopened.supervisor_event_head() == second
    assert len(reopened.pending_supervisor_events()) == 2


def test_commit_growth_decision_consumes_cursor_and_creates_leases(
    store: ResearchStore,
):
    root, episode = _seed_root_episode(store)
    head = store.emit_supervisor_event("root_ready", {"root_node_id": root.node_id})

    commit = store.commit_supervisor_decision(
        decision_id="d1",
        work_id=f"supervisor-{head}",
        node_ids=[root.node_id],
        rationale="root deserves a first lease",
        cursor_to=head,
        leases=[LeaseSpec(
            node_id=root.node_id,
            episode_id=episode.episode_id,
            proposal_slots=2,
        )],
    )

    assert commit.replayed is False
    assert store.supervisor_event_cursor() == head
    assert store.pending_supervisor_events() == []
    (allocation,) = commit.allocations
    assert allocation.node_id == root.node_id
    assert len(allocation.reserved_proposal_ids) == 2
    assert allocation.decision_id == "d1"
    decision = store.get_supervisor_decision("d1")
    assert decision["node_ids"] == [root.node_id]
    assert decision["decision_kind"] == "growth"
    accepted = store.latest_scheduler_event("supervisor_decision_accepted")
    assert accepted["decision_id"] == "d1"


def test_commit_rejects_stale_cursor_atomically(store: ResearchStore):
    root, episode = _seed_root_episode(store)
    head = store.emit_supervisor_event("root_ready", {"root_node_id": root.node_id})
    store.emit_supervisor_event(
        "experiment_terminal",
        {"experiment_id": "e1", "status": "no_change"},
    )

    with pytest.raises(StaleSupervisorDecision):
        store.commit_supervisor_decision(
            decision_id="d1",
            work_id=f"supervisor-{head}",
            node_ids=[root.node_id],
            rationale="stale",
            cursor_to=head,
            leases=[LeaseSpec(root.node_id, episode.episode_id, 1)],
        )

    assert store.get_supervisor_decision("d1") is None
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []
    assert len(store.pending_supervisor_events()) == 2


def test_commit_is_idempotent_on_decision_id(store: ResearchStore):
    root, episode = _seed_root_episode(store)
    head = store.emit_supervisor_event("root_ready", {"root_node_id": root.node_id})
    leases = [LeaseSpec(root.node_id, episode.episode_id, 1)]
    first = store.commit_supervisor_decision(
        decision_id="d1",
        work_id=f"supervisor-{head}",
        node_ids=[root.node_id],
        rationale="once",
        cursor_to=head,
        leases=leases,
    )

    # A new event lands after the commit; re-delivering the same decision
    # must be a no-op replay, not a stale rejection.
    store.emit_supervisor_event(
        "lease_terminal", {"allocation_id": "a1", "outcome": "abstain"})
    second = store.commit_supervisor_decision(
        decision_id="d1",
        work_id=f"supervisor-{head}",
        node_ids=[root.node_id],
        rationale="once",
        cursor_to=head,
        leases=leases,
    )

    assert second.replayed is True
    assert [a.allocation_id for a in second.allocations] == [
        a.allocation_id for a in first.allocations
    ]
    assert len(store.open_allocations()) == 1


def test_empty_growth_decision_consumes_cursor_without_leases(
    store: ResearchStore,
):
    root, _ = _seed_root_episode(store)
    head = store.emit_supervisor_event("root_ready", {"root_node_id": root.node_id})

    commit = store.commit_supervisor_decision(
        decision_id="d-wait",
        work_id=f"supervisor-{head}",
        node_ids=[],
        rationale="wait for sibling evidence",
        cursor_to=head,
    )

    assert commit.allocations == ()
    assert store.supervisor_event_cursor() == head
    assert store.open_allocations() == []


def test_commit_integration_decision_creates_request_atomically(
    store: ResearchStore,
):
    root, _ = _seed_root_episode(store)
    head = store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    epoch = store.current_epoch()
    request = {
        "integration_request_id": "req-1",
        "epoch_id": epoch.epoch_id,
        "target_node_id": root.node_id,
        "donor_experiment_ids": ("exp-donor",),
        "selection_rationale": "branches matured",
    }

    store.commit_supervisor_decision(
        decision_id="d-int", work_id=f"supervisor-{head}",
        decision_kind="integration_request", rationale="matured",
        detail=dict(request), cursor_to=head, integration_request=request,
    )

    created = store.get_integration_request("req-1")
    assert created is not None and created.status == "open"
    assert store.supervisor_event_cursor() == head
    assert store.get_supervisor_decision("d-int")["decision_kind"] == (
        "integration_request")
    assert store.latest_scheduler_event(
        "integration_request_created")["integration_request_id"] == "req-1"

    # Re-delivering the same decision is a replay: the request row and the
    # accepted event are not duplicated.
    store.commit_supervisor_decision(
        decision_id="d-int", work_id=f"supervisor-{head}",
        decision_kind="integration_request", rationale="matured",
        detail=dict(request), cursor_to=head, integration_request=request,
    )
    assert store.latest_scheduler_event(
        "integration_request_created")["integration_request_id"] == "req-1"
    with store._connect() as conn:
        created_count = conn.execute(
            "SELECT COUNT(*) FROM integration_requests").fetchone()[0]
        accepted_count = conn.execute(
            "SELECT COUNT(*) FROM scheduler_events "
            "WHERE type = 'supervisor_decision_accepted'").fetchone()[0]
    assert created_count == 1
    assert accepted_count == 1


def test_commit_rejects_mixed_decision_payloads(store: ResearchStore):
    root, _ = _seed_root_episode(store)
    head = store.emit_supervisor_event("root_ready", {"root_node_id": root.node_id})

    # Only growth decisions select nodes or create leases.
    with pytest.raises(ValueError, match="only growth"):
        store.commit_supervisor_decision(
            decision_id="d-bad", work_id=f"supervisor-{head}",
            decision_kind="integration_request", node_ids=[root.node_id],
            rationale="mixed", cursor_to=head,
        )
    # A kind without its side-effect payload fails fast, before any write.
    with pytest.raises(ValueError, match="request payload"):
        store.commit_supervisor_decision(
            decision_id="d-bad2", work_id=f"supervisor-{head}",
            decision_kind="integration_request", rationale="empty",
            cursor_to=head,
        )
    with pytest.raises(ValueError, match="unknown decision kind"):
        store.commit_supervisor_decision(
            decision_id="d-bad3", work_id=f"supervisor-{head}",
            decision_kind="hybrid", rationale="invalid", cursor_to=head,
        )
    assert store.get_supervisor_decision("d-bad") is None
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []


def test_budget_change_event_shares_the_limit_transaction(
    store: ResearchStore,
):
    store.install_run_limits(
        {"max_terminal_evals": 5, "budget_usd": 2.0})
    # Constructing the run is not an intervention: no wake event.
    assert store.pending_supervisor_events() == []

    changed = store.install_run_limits(
        {"max_terminal_evals": 5, "budget_usd": 1.0})

    assert changed == ["budget_usd"]
    events = store.pending_supervisor_events()
    assert [e.type for e in events] == ["budget_changed"]
    assert events[0].payload["budget_usd"] == 1.0
    assert events[0].payload["changed"] == ["budget_usd"]
    # One transaction: a reopen sees the new value AND the event, or
    # neither — there is no crash window between them.
    reopened = ResearchStore(store.path)
    assert reopened.run_limits()["budget_usd"] == 1.0
    assert [e.type for e in reopened.pending_supervisor_events()] == [
        "budget_changed"]
