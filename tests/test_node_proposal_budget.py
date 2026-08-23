"""Node-lifetime Proposal capacity and allocation reservations.

The per-node proposal cap is dissolved for scheduling (seat design v4);
these tests pin the store-level reservation mechanics, which remain for
explicit callers that pass ``max_proposals_per_node``."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


@pytest.fixture
def store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path / "simpleevo.db")


def _node_with_episodes(store: ResearchStore, count: int = 3):
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episodes = [tx.create_episode(node_id=node.node_id) for _ in range(count)]
    return node, episodes


def test_node_budget_counts_open_reservations(store):
    node, (first, second, _third) = _node_with_episodes(store)
    a1 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=first.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    a2 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=second.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    assert len(a1.reserved_proposal_ids) == 3
    assert len(a2.reserved_proposal_ids) == 1


def test_closing_allocation_releases_unused_reservations(store):
    node, (first, second, _third) = _node_with_episodes(store)
    a1 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=first.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    store.publish_proposals(
        node_id=node.node_id,
        episode_id=first.episode_id,
        proposals=[{
            "proposal_id": a1.reserved_proposal_ids[0],
            "instruction": "try one direction",
            "rationale": {},
        }],
        reserved_proposal_ids=a1.reserved_proposal_ids,
    )
    store.deallocate_proposer(
        allocation_id=a1.allocation_id,
        proposals_produced=1,
    )
    a2 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=second.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    assert len(a2.reserved_proposal_ids) == 3


def test_scheduler_no_longer_skips_on_published_proposals(store):
    """Seat design §4: the per-node proposal cap is dissolved for
    scheduling — published proposals never make a node unallocatable; the
    budget is the only boundary."""
    node, (episode, _second, _third) = _node_with_episodes(store)
    store.publish_proposals(
        node_id=node.node_id,
        episode_id=episode.episode_id,
        proposals=[{
            "proposal_id": "published-1",
            "instruction": "already queued",
            "rationale": {},
        }],
    )
    evolution = EvolutionConfig(
        goal="test",
        repo_path=Path("/x"),
        runtime_image=Path("/y"),
        editable_paths=(),
        frozen_paths=(),
        eval_commands=(),
        metrics_schema={"objective": {"key": "total_ms"}},
        axes=("total_ms",),
    )
    scheduler = Scheduler(
        store,
        store.path.parent,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=0),
        evolution_config=evolution,
    )
    scheduler.submit_proposer = lambda _aid, _payload: ""
    assert len(
        scheduler._allocate_proposers(scheduler._compute_frontier())) == 1
    assert len(store.open_allocations()) == 1


def test_concurrent_allocations_cannot_exceed_node_budget(store):
    node, episodes = _node_with_episodes(store, count=6)

    def allocate(episode):
        return store.allocate_proposer(
            node_id=node.node_id,
            episode_id=episode.episode_id,
            proposal_slots=3,
            max_proposals_per_node=4,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        allocations = list(executor.map(allocate, episodes))
    reserved = sum(
        len(allocation.reserved_proposal_ids)
        for allocation in allocations
        if allocation is not None
    )
    assert reserved == 4
