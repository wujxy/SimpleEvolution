"""Programmatic reseed: a frontier node whose episodes are all terminal is
re-studied with a fresh episode (inheriting its most recent final cognition),
bounded by ``max_research_per_node``."""
from __future__ import annotations

import tempfile
from pathlib import Path

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import GateDecision, ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


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
        from simpleevo.db.queries import ResearchQueries
        queries = ResearchQueries(run_dir / "simpleevo.db")
        episodes = queries.episodes_for_node(root.node_id, limit=1000)
        # ordered last_active_at DESC -> e3, e2, e1
        assert len(episodes) == 3
        e3, e2, e1 = episodes
        assert e2.inherited_from_episode_id == e1.episode_id
        assert e3.inherited_from_episode_id == e2.episode_id
        assert e1.inherited_from_episode_id is None
