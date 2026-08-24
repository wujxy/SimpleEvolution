"""Complete-research lease lifecycle store tests (科学家完整研究制 §2.3/2.4)."""
from __future__ import annotations

import json

import pytest

from simpleevo.contracts import GateDecision, GateResult
from simpleevo.db.store import (
    LeaseSpec,
    ResearchStore,
    VacuousExitError,
)


@pytest.fixture()
def env(tmp_path):
    store = ResearchStore(tmp_path / "test.db")
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-root",
            metrics={"lookups_per_sec": 100.0},
            gate_result=GateDecision({}, True), depth=0, status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=node.node_id,
            variation_operator="lens-x",
        )
    allocation = store.allocate_proposer(
        node_id=node.node_id, episode_id=episode.episode_id, lens="lens-x",
    )
    # The seat registered at least one state row (incremental upsert
    # contract) before concluding.
    from simpleevo.db.lease_writer import upsert_lease_research_state

    upsert_lease_research_state(
        store.path, lease_id=allocation.allocation_id,
        episode_id=episode.episode_id, node_id=node.node_id,
        working_model="cache misses dominate",
    )
    return store, node, episode, allocation


def _events(store, table, kind):
    with store.transaction() as tx:
        rows = tx._conn.execute(
            f"SELECT payload FROM {table} WHERE type = ? "
            "ORDER BY rowid", (kind,),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def test_deliver_mints_adjudication_experiment_atomically(env):
    store, node, episode, allocation = env
    ingest = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "a" * 40,
            "handover": "dead ends: none yet. open questions: everything.",
        },
        with_attempt=True,
    )
    assert ingest.replayed is False
    assert ingest.proposal_id == allocation.reserved_proposal_ids[0]
    assert ingest.experiment_id == f"exp-{ingest.proposal_id}"
    assert ingest.attempt_id is not None

    proposal = store.get_proposal(ingest.proposal_id)
    # Minted 'running' — a queued delivery could be demoted by the
    # executor queue's overflow bound and strand the lease forever.
    assert proposal.status == "running"
    assert proposal.research_state_id == f"rs-{episode.episode_id}-head"

    with store.transaction() as tx:
        experiment = tx.get_experiment(ingest.experiment_id)
        assert experiment.parent_node_id == node.node_id
        assert experiment.status == "running"

    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT state FROM proposer_allocations WHERE allocation_id = ?",
            (allocation.allocation_id,),
        ).fetchone()
    assert row["state"] == "awaiting_adjudication"


def test_deliver_replay_is_a_noop(env):
    store, node, episode, allocation = env
    conclusion = {
        "kind": "deliver", "node_id": node.node_id,
        "episode_id": episode.episode_id,
        "world_sha": "b" * 40, "handover": "one line",
    }
    first = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id, conclusion=conclusion,
    )
    second = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id, conclusion=conclusion,
    )
    assert second.replayed is True
    assert second.proposal_id == first.proposal_id
    assert second.experiment_id == first.experiment_id
    with store.transaction() as tx:
        n = tx._conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE episode_id = ?",
            (episode.episode_id,),
        ).fetchone()
    assert n["n"] == 1


def test_exit_without_registered_state_is_vacuous(tmp_path):
    store = ResearchStore(tmp_path / "t.db")
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-r",
            metrics={}, gate_result=GateDecision({}, True), depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
    allocation = store.allocate_proposer(
        node_id=node.node_id, episode_id=episode.episode_id,
    )
    with pytest.raises(VacuousExitError):
        store.ingest_lease_conclusion(
            allocation_id=allocation.allocation_id,
            conclusion={
                "kind": "abstain", "node_id": node.node_id,
                "episode_id": episode.episode_id,
            },
        )


def test_delivered_sha_colliding_with_a_node_is_rejected(env):
    store, node, episode, allocation = env
    colliding_sha = "9" * 40
    with store.transaction() as tx:
        tx.create_node(
            parent_node_id=node.node_id, experiment_id=None,
            sha=colliding_sha, metrics={},
            gate_result=GateDecision({}, True), depth=1, status="active",
        )
    with pytest.raises(ValueError, match="already exists as a node"):
        store.ingest_lease_conclusion(
            allocation_id=allocation.allocation_id,
            conclusion={
                "kind": "deliver", "node_id": node.node_id,
                "episode_id": episode.episode_id,
                "world_sha": colliding_sha, "handover": "x",
            },
        )


def test_handover_hard_cap(env):
    store, node, episode, allocation = env
    conclusion = {
        "kind": "deliver", "node_id": node.node_id,
        "episode_id": episode.episode_id,
        "world_sha": "c" * 40,
        "handover": "word " * 700,
    }
    with pytest.raises(ValueError, match="hard cap"):
        store.ingest_lease_conclusion(
            allocation_id=allocation.allocation_id, conclusion=conclusion,
        )
    # Degraded delivery (worker already retried twice, marks itself
    # non-compliant) must not be blocked: SHA 与裁决解耦.
    conclusion["handover_compliant"] = False
    ingest = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id, conclusion=conclusion,
    )
    assert ingest.replayed is False


def test_abstain_and_cut_off_conclude_with_outcome_events(env):
    store, node, episode, allocation = env
    store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "abstain", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "axes_checked": ["axis-a"],
        },
    )
    terminal = _events(store, "supervisor_events", "lease_terminal")
    assert terminal[-1]["outcome"] == "abstain"
    assert terminal[-1]["allocation_id"] == allocation.allocation_id
    with store.transaction() as tx:
        episode_row = tx.get_episode(episode.episode_id)
    assert episode_row.conclusion_type == "abstain"
    assert episode_row.concluded_at is not None


def test_gate_reject_reopens_with_writeback_and_bounded_budget(env):
    store, node, episode, allocation = env
    ingest = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "d" * 40, "handover": "first",
        },
    )
    gate = GateDecision(
        results={"VERIFY": GateResult(False, "checksum mismatch")},
        passed=False,
    )
    assert store.record_lease_adjudication(
        allocation_id=allocation.allocation_id,
        experiment_id=ingest.experiment_id, gate_result=gate,
        max_reopens=2,
    ) is True
    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT state, reopen_count FROM proposer_allocations "
            "WHERE allocation_id = ?",
            (allocation.allocation_id,),
        ).fetchone()
    assert row["state"] == "reopen"
    assert row["reopen_count"] == 1
    feedback = store._read.lease_adjudication_for_episode(episode.episode_id)
    assert feedback["experiment_id"] == ingest.experiment_id
    assert feedback["gate"]["VERIFY"]["passed"] is False

    # Reactivate → second delivery (deterministic second id) → second
    # reject exhausts the reopen budget.
    store.reactivate_lease(allocation.allocation_id)
    ingest2 = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "e" * 40, "handover": "second",
        },
    )
    assert ingest2.proposal_id == f"delivery-{allocation.allocation_id}-2"
    assert store.record_lease_adjudication(
        allocation_id=allocation.allocation_id,
        experiment_id=ingest2.experiment_id, gate_result=gate,
        max_reopens=2,
    ) is True
    store.reactivate_lease(allocation.allocation_id)
    ingest3 = store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "f" * 40, "handover": "third",
        },
    )
    assert store.record_lease_adjudication(
        allocation_id=allocation.allocation_id,
        experiment_id=ingest3.experiment_id, gate_result=gate,
        max_reopens=2,
    ) is False

    store.conclude_lease(
        allocation_id=allocation.allocation_id, outcome="rejected",
        reason="reopen budget exhausted",
    )
    terminal = _events(store, "supervisor_events", "lease_terminal")
    assert terminal[-1]["outcome"] == "rejected"
    assert terminal[-1]["reopen_count"] == 2
    concluded = _events(store, "scheduler_events", "lease_concluded")
    assert concluded[-1]["reason"] == "reopen budget exhausted"


def test_capacity_counts_researching_leases_only(env):
    store, node, episode, allocation = env
    queries = store._read
    assert queries.researching_open_allocation_count() == 1
    store.ingest_lease_conclusion(
        allocation_id=allocation.allocation_id,
        conclusion={
            "kind": "deliver", "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "world_sha": "1" * 40, "handover": "x",
        },
    )
    # Parked for adjudication: not researching, but still open.
    assert queries.researching_open_allocation_count() == 0
    assert queries.open_allocation_count() == 1
    assert queries.awaiting_adjudication_allocations()[0].allocation_id \
        == allocation.allocation_id
