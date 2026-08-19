"""Tests for frontier computation and proposer sampling."""
from __future__ import annotations

import pytest

from simpleevo.db.store import GateDecision, GateResult, Node
from simpleevo.scheduler.frontier import (
    FrontierConfig,
    GepaPolicy,
    TopKPolicy,
    build_policy,
    compute_frontier,
    sample_proposer_nodes,
)


def _node(node_id: str, metrics: dict, depth: int = 0) -> Node:
    return Node(
        node_id=node_id,
        parent_node_id=None,
        experiment_id=None,
        sha=f"sha-{node_id}",
        metrics=metrics,
        gate_result=GateDecision({}, True),
        depth=depth,
        status="active",
        created_at=0.0,
    )


# ---------------------------------------------------------------------------
# GEPA policy: per-axis best + dominated prune
# ---------------------------------------------------------------------------


def test_frontier_union_of_axis_winners():
    nodes = [
        _node("n1", {"total_ms": 100.0, "qmle_ms": 50.0}),
        _node("n2", {"total_ms": 90.0, "qmle_ms": 60.0}),
    ]
    config = FrontierConfig(axes=("total_ms", "qmle_ms"))
    frontier = compute_frontier(nodes, [], config)
    assert "n2" in frontier  # wins total_ms
    assert "n1" in frontier  # wins qmle_ms


def test_gepa_keeps_only_per_axis_best():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 100.5}),
        _node("n3", {"total_ms": 105.0}),
    ]
    config = FrontierConfig(axes=("total_ms",))
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier
    assert "n2" not in frontier  # dominated by n1
    assert "n3" not in frontier  # dominated by n1


def test_gepa_keeps_exact_statistical_tie():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 100.0}),
        _node("n3", {"total_ms": 101.0}),
    ]
    config = FrontierConfig(axes=("total_ms",))
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier
    assert "n2" in frontier  # exact tie -> both retained
    assert "n3" not in frontier


def test_gepa_always_takes_per_axis_best_over_incumbent():
    """Fix-① regression: a faster node dethrones the incumbent even when the
    gap is tiny. (The old hysteresis kept the incumbent unless beaten by more
    than ``hysteresis_margin`` — the d2/d3 stall in tiny_test_04.)"""
    from simpleevo.db.store import FrontierAxis

    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 99.5}),
    ]
    current = [FrontierAxis("total_ms", "n1", 100.0, 0.0, None, 0.0)]
    config = FrontierConfig(axes=("total_ms",))
    frontier = compute_frontier(nodes, current, config)
    assert "n2" in frontier
    assert "n1" not in frontier


def test_gepa_big_beat_wins():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 90.0}),
    ]
    config = FrontierConfig(axes=("total_ms",))
    frontier = compute_frontier(nodes, [], config)
    assert "n2" in frontier
    assert "n1" not in frontier


def test_gepa_dominated_prune_multi_axis():
    nodes = [
        _node("nA", {"total_ms": 100.0, "qmle_ms": 50.0}),
        _node("nB", {"total_ms": 80.0, "qmle_ms": 40.0}),
    ]
    config = FrontierConfig(axes=("total_ms", "qmle_ms"))
    frontier = compute_frontier(nodes, [], config)
    assert "nB" in frontier
    assert "nA" not in frontier  # strictly dominated on both axes
    # The axes map must not keep the pruned node (store persists from it).
    assert frontier.axes["qmle_ms"] == frozenset({"nB"})


def test_gepa_keeps_non_dominated_pareto():
    nodes = [
        _node("n1", {"total_ms": 100.0, "qmle_ms": 50.0}),
        _node("n2", {"total_ms": 90.0, "qmle_ms": 60.0}),
    ]
    config = FrontierConfig(axes=("total_ms", "qmle_ms"))
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier  # wins qmle_ms
    assert "n2" in frontier  # wins total_ms; neither dominates


def test_gepa_higher_is_better_direction():
    nodes = [
        _node("n1", {"throughput": 1.0}),
        _node("n2", {"throughput": 2.0}),
    ]
    config = FrontierConfig(
        axes=("throughput",),
        schema={"throughput": {"lower_is_better": False}},
    )
    frontier = compute_frontier(nodes, [], config)
    assert "n2" in frontier  # higher is better
    assert "n1" not in frontier


# ---------------------------------------------------------------------------
# Top-K policy
# ---------------------------------------------------------------------------


def test_topk_keeps_top_k_per_axis():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 100.5}),
        _node("n3", {"total_ms": 105.0}),
    ]
    config = FrontierConfig(axes=("total_ms",), policy=TopKPolicy(k=2))
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier
    assert "n2" in frontier
    assert "n3" not in frontier


def test_topk_union_across_axes():
    nodes = [
        _node("n1", {"total_ms": 100.0, "qmle_ms": 50.0}),
        _node("n2", {"total_ms": 90.0, "qmle_ms": 60.0}),
    ]
    config = FrontierConfig(
        axes=("total_ms", "qmle_ms"), policy=TopKPolicy(k=1)
    )
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier  # wins qmle_ms top-1
    assert "n2" in frontier  # wins total_ms top-1


def test_topk_direction_respects_schema():
    nodes = [
        _node("n1", {"throughput": 1.0}),
        _node("n2", {"throughput": 2.0}),
    ]
    config = FrontierConfig(
        axes=("throughput",),
        policy=TopKPolicy(k=1),
        schema={"throughput": {"lower_is_better": False}},
    )
    frontier = compute_frontier(nodes, [], config)
    assert "n2" in frontier
    assert "n1" not in frontier


def test_topk_skips_non_finite():
    import math

    nodes = [
        _node("n1", {"total_ms": float("nan")}),
        _node("n2", {"total_ms": math.inf}),
        _node("n3", {"total_ms": 100.0}),
        _node("n4", {}),
    ]
    config = FrontierConfig(axes=("total_ms",), policy=TopKPolicy(k=2))
    frontier = compute_frontier(nodes, [], config)
    assert frontier.node_ids == frozenset({"n3"})


def test_gepa_skips_non_finite():
    import math

    nodes = [
        _node("n1", {"total_ms": float("nan")}),
        _node("n2", {"total_ms": 100.0}),
    ]
    config = FrontierConfig(axes=("total_ms",))
    frontier = compute_frontier(nodes, [], config)
    assert frontier.node_ids == frozenset({"n2"})


# ---------------------------------------------------------------------------
# Bootstrap + factory
# ---------------------------------------------------------------------------


def test_bootstrap_uses_roots_when_no_measurements():
    nodes = [
        _node("r1", {}, depth=0),
        _node("r2", {}, depth=0),
        _node("c1", {}, depth=1),
    ]
    for policy in (GepaPolicy(), TopKPolicy(k=3)):
        config = FrontierConfig(axes=("total_ms",), policy=policy)
        frontier = compute_frontier(nodes, [], config)
        assert frontier.node_ids == frozenset({"r1", "r2"})


def test_build_policy_resolves():
    assert isinstance(build_policy("gepa"), GepaPolicy)
    assert isinstance(build_policy("topk", top_k=5), TopKPolicy)
    assert isinstance(build_policy("unknown"), GepaPolicy)  # safe default


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_sample_weighted_by_axis_count():
    class FakeFrontier:
        node_ids = frozenset({"a", "b"})

        def axis_count(self, node_id: str) -> int:
            return {"a": 2, "b": 1}[node_id]

    allocations: dict[str, int] = {}
    samples = sample_proposer_nodes(
        FakeFrontier(), allocations, 100, FrontierConfig(axes=())
    )
    a_count = sum(1 for s in samples if s == "a")
    b_count = sum(1 for s in samples if s == "b")
    assert a_count > b_count
