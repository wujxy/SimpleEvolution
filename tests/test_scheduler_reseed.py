"""Programmatic reseed: a frontier node whose episodes are all terminal is
re-studied with a fresh episode (inheriting its most recent final cognition),
bounded by ``max_research_per_node``.  With ``generator_reseed`` on, each
re-study also carries a sampled variation operator (untried per node)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig

_FIXTURE_BASIS = [
    Generator(id="G1", name="跨域同构移植", description="d1"),
    Generator(id="G2", name="分解", description="d2"),
    Generator(id="G3", name="理想化", description="d3"),
    Generator(id="G4", name="对称提升", description="d4"),
]


def test_reseed_on_allocation_bounded_by_max_research_per_node():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
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

        evolution = EvolutionConfig(
            goal="test",
            repo_path=Path("/x"),
            runtime_image=Path("/y"),
            editable_paths=(),
            frozen_paths=(),
            eval_commands=(),
            metrics_schema={"objective": {"key": "total_ms", "lower_is_better": True}},
            axes=("total_ms",),
            max_research_per_node=3,
        )
        scheduler = Scheduler(
            store,
            run_dir,
            SchedulerConfig(
                max_proposer_inflight=1,
                max_experiment_inflight=0,
                poll_seconds=0.0,
            ),
            evolution_config=evolution,
        )

        # Frontier is the single root (it has a measured value). Three re-studies
        # happen (fresh + two reseeds), then the budget is exhausted.
        produced: list[int] = []
        for i in range(1, 5):
            frontier = scheduler._compute_frontier()
            jobs = scheduler._allocate_proposers(frontier)
            produced.append(len(jobs))
            if jobs:
                store.deallocate_proposer(allocation_id=jobs[0], proposals_produced=1)
            store.mark_running_attempts_lost()  # free the single proposer slot

        assert produced == [1, 1, 1, 0]
        assert store.count_allocations_for_node(root.node_id) == 3

        # Inheritance chain: each reseed inherits the node's most recent episode.
        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        # ordered last_active_at DESC -> e3, e2, e1
        assert len(episodes) == 3
        e3, e2, e1 = episodes
        assert e2.inherited_from_episode_id == e1.episode_id
        assert e3.inherited_from_episode_id == e2.episode_id
        assert e1.inherited_from_episode_id is None


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


def _make_evolution(*, generator_reseed: bool) -> EvolutionConfig:
    return EvolutionConfig(
        goal="test",
        repo_path=Path("/x"),
        runtime_image=Path("/y"),
        editable_paths=(),
        frozen_paths=(),
        eval_commands=(),
        metrics_schema={"objective": {"key": "total_ms", "lower_is_better": True}},
        axes=("total_ms",),
        max_research_per_node=3,
        generator_reseed=generator_reseed,
    )


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


def test_reseed_attaches_untried_variation_operators():
    """generator_reseed=True: reseeded episodes carry a sampled variation
    operator drawn from the untried-per-node set; the proposer payload carries
    the resolved directives. The fresh (first) episode carries none."""
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
            evolution_config=_make_evolution(generator_reseed=True),
            generator_basis=_FIXTURE_BASIS,
        )
        captured = _drive_allocations(scheduler, store, 3)
        assert store.count_allocations_for_node(root.node_id) == 3

        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        e3, e2, e1 = episodes  # last_active_at DESC
        assert e1.variation_operator is None  # first episode: no generator
        assert e2.variation_operator is not None
        assert e3.variation_operator is not None
        # Untried-per-node sampling: the two reseeds draw from disjoint sets.
        assert set(e2.variation_operator.split("+")).isdisjoint(
            e3.variation_operator.split("+")
        )

        # Fresh allocation carries no directives; reseeds carry resolved ones.
        assert captured[0]["variation_operators"] == []
        assert captured[1]["variation_operators"]
        assert all(
            {"id", "name", "description"} <= set(d)
            for d in captured[1]["variation_operators"]
        )


def test_reseed_without_generator_keeps_variation_operator_none():
    """generator_reseed=False (the default) preserves the old behavior: reseed
    episodes carry no variation operator and the payload carries none."""
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
            evolution_config=_make_evolution(generator_reseed=False),
            generator_basis=_FIXTURE_BASIS,
        )
        captured = _drive_allocations(scheduler, store, 2)  # fresh + one reseed
        assert store.count_allocations_for_node(root.node_id) == 2

        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        e2, e1 = episodes
        assert e1.variation_operator is None
        assert e2.variation_operator is None
        assert captured[0]["variation_operators"] == []
        assert captured[1]["variation_operators"] == []
