"""Programmatic reseed: a frontier-baseline node whose episodes are all
terminal is re-studied with a fresh episode inheriting its most recent
final cognition.  Seat design v6: no research budget bounds this (the
Supervisor's purchase prices seat count; the budget is the boundary), and
no variation operator is suggested — lenses arrive via supervisor seat
purchases only."""
from __future__ import annotations

import tempfile
from pathlib import Path

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator
from simpleevo.host.wake import build_wake_view
from simpleevo.generator import load_generator_basis
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig

_FIXTURE_BASIS = [
    Generator(id="G1", name="跨域同构移植", description="d1"),
    Generator(id="G2", name="分解", description="d2"),
    Generator(id="G3", name="理想化", description="d3"),
    Generator(id="G4", name="对称提升", description="d4"),
]


def _evolution() -> EvolutionConfig:
    return EvolutionConfig(
        goal="test",
        repo_path=Path("/x"),
        runtime_image=Path("/y"),
        editable_paths=(),
        frozen_paths=(),
        eval_commands=(),
        metrics_schema={"objective": {"key": "total_ms", "lower_is_better": True}},
        axes=("total_ms",),
    )


def _root_with_first_episode(store) -> tuple:
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        e1 = tx.create_episode(inherited_from_episode_id=None, node_id=root.node_id)
    return root, e1


def _drive_allocations(scheduler, store, n: int) -> list[dict]:
    captured: list[dict] = []
    scheduler.submit_proposer = lambda _aid, payload: captured.append(payload)
    for _ in range(n):
        frontier = scheduler._compute_frontier()
        jobs = scheduler._allocate_proposers(frontier)
        if jobs:
            store.deallocate_proposer(allocation_id=jobs[0], proposals_produced=1)
        store.mark_running_attempts_lost()
    return captured


def test_frontier_reseed_is_unbounded_and_inherits():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, _e1 = _root_with_first_episode(store)
        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(
                max_proposer_inflight=1,
                max_experiment_inflight=0,
                poll_seconds=0.0,
            ),
            evolution_config=_evolution(),
        )

        # The dissolved per-node budget no longer stops re-study: every
        # round allocates on the single frontier node.
        produced: list[int] = []
        for _ in range(4):
            frontier = scheduler._compute_frontier()
            jobs = scheduler._allocate_proposers(frontier)
            produced.append(len(jobs))
            if jobs:
                store.deallocate_proposer(allocation_id=jobs[0], proposals_produced=1)
            store.mark_running_attempts_lost()  # free the single proposer slot

        assert produced == [1, 1, 1, 1]

        # Inheritance chain: each frontier reseed inherits the node's most
        # recent episode (GEPA pool semantics — study, not seat).
        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        # ordered last_active_at DESC -> e4, e3, e2, e1
        assert len(episodes) == 4
        e4, e3, e2, e1 = episodes
        assert e2.inherited_from_episode_id == e1.episode_id
        assert e3.inherited_from_episode_id == e2.episode_id
        assert e4.inherited_from_episode_id == e3.episode_id
        assert e1.inherited_from_episode_id is None


def test_frontier_reseed_carries_no_lens_suggestion():
    """Frontier-baseline leases carry no seat block and no operator
    suggestion: lens identity arrives only with supervisor seat purchases."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        root, _e1 = _root_with_first_episode(store)
        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(
                max_proposer_inflight=1,
                max_experiment_inflight=0,
                poll_seconds=0.0,
            ),
            evolution_config=_evolution(),
            generator_basis=_FIXTURE_BASIS,
        )
        captured = _drive_allocations(scheduler, store, 2)  # fresh + reseed
        assert store.count_allocations_for_node(root.node_id) == 2
        assert len(captured) == 2

        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        assert all(e.variation_operator is None for e in episodes)
        for payload in captured:
            assert "seat" not in payload
            assert "suggested_operator_id" not in payload
            assert "generator_basis" not in payload
            assert build_wake_view(
                ResearchQueries(run_dir / "simpleevo.db"),
                load_generator_basis(),
                node_id=payload["node_id"],
                episode_id=payload["episode_id"],
            )["seat"] is None
