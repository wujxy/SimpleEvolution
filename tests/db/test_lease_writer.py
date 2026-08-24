"""Worker-side lease_writer tests: incremental upsert + assistant ledger."""
from __future__ import annotations

import json

import pytest

from simpleevo.contracts import GateDecision
from simpleevo.db.lease_writer import (
    append_experiment_log_entry,
    mark_call_adopted,
    record_assistant_call,
    upsert_lease_research_state,
)
from simpleevo.db.store import ResearchStore


@pytest.fixture()
def env(tmp_path):
    store = ResearchStore(tmp_path / "t.db")
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-r",
            metrics={}, gate_result=GateDecision({}, True), depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
    return store, node, episode


def test_upsert_revises_one_head_row(env):
    store, node, episode = env
    lease = "alloc-1"
    r1 = upsert_lease_research_state(
        store.path, lease_id=lease, episode_id=episode.episode_id,
        node_id=node.node_id, working_model="first model",
    )
    r2 = upsert_lease_research_state(
        store.path, lease_id=lease, episode_id=episode.episode_id,
        node_id=node.node_id, working_model="second model",
        evidence=[{"claim": "x", "status": "belief"}],
        experiment_log=[{"intent": "try", "verdict": "worse"}],
    )
    assert (r1, r2) == (1, 2)
    head = store._read.research_state_head(episode.episode_id)
    assert head.research_state_id == f"rs-{episode.episode_id}-head"
    assert head.revision == 2
    assert head.working_model == "second model"
    assert head.evidence[0]["status"] == "belief"
    assert head.experiment_log[0]["verdict"] == "worse"
    assert head.lease_id == lease


def test_upsert_interleaves_with_store_writes(env):
    """The worker writer and the scheduler's single writer serialize."""
    store, node, episode = env
    lease = "alloc-2"
    upsert_lease_research_state(
        store.path, lease_id=lease, episode_id=episode.episode_id,
        node_id=node.node_id, working_model="v1",
    )
    # A scheduler-side write between two worker upserts.
    with store.transaction() as tx:
        tx.update_episode_last_active(episode.episode_id, 123.0)
    r = upsert_lease_research_state(
        store.path, lease_id=lease, episode_id=episode.episode_id,
        node_id=node.node_id, working_model="v2",
    )
    assert r == 2


def test_append_experiment_log_entry_bumps_revision(env):
    store, node, episode = env
    lease = "alloc-3"
    upsert_lease_research_state(
        store.path, lease_id=lease, episode_id=episode.episode_id,
        node_id=node.node_id, working_model="m",
    )
    append_experiment_log_entry(
        store.path, lease_id=lease,
        entry={"intent": "prefetch", "sha": "a" * 40, "verdict": "faster"},
    )
    head = store._read.research_state_head(episode.episode_id)
    assert head.revision == 2
    assert head.experiment_log[-1]["intent"] == "prefetch"


def test_assistant_call_ledger_round_trip(env):
    store, node, episode = env
    record_assistant_call(
        store.path, call_id="call-1", episode_id=episode.episode_id,
        lease_id="alloc-4", lens="lens-x", kind="consult",
        question_digest="is there prior art on bucketed lookup?",
        adopted=None, usage={"input_tokens": 100, "output_tokens": 50},
    )
    mark_call_adopted(store.path, call_id="call-1", adopted=True)
    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT * FROM assistant_calls WHERE call_id = 'call-1'"
        ).fetchone()
    assert row["kind"] == "consult"
    assert row["adopted"] == 1
    assert json.loads(row["usage"])["input_tokens"] == 100
