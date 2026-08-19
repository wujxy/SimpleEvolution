"""Tests for frontier computation and proposer sampling."""
from __future__ import annotations

import pytest

from simpleevo.db.store import GateDecision, GateResult, Node
from simpleevo.scheduler.frontier import FrontierConfig, compute_frontier, sample_proposer_nodes


def _node(node_id: str, metrics: dict) -> Node:
    return Node(
        node_id=node_id,
        parent_node_id=None,
        experiment_id=None,
        sha=f"sha-{node_id}",
        metrics=metrics,
        gate_result=GateDecision({}, True),
        depth=0,
        status="active",
        created_at=0.0,
    )


def test_frontier_union_of_axis_winners():
    nodes = [
        _node("n1", {"total_ms": 100.0, "qmle_ms": 50.0}),
        _node("n2", {"total_ms": 90.0, "qmle_ms": 60.0}),
    ]
    config = FrontierConfig(axes=("total_ms", "qmle_ms"))
    frontier = compute_frontier(nodes, [], config)
    assert "n2" in frontier  # wins total_ms
    assert "n1" in frontier  # wins qmle_ms


def test_tie_band_includes_near_best():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 100.5}),
        _node("n3", {"total_ms": 105.0}),
    ]
    config = FrontierConfig(axes=("total_ms",), tie_band=1.0)
    frontier = compute_frontier(nodes, [], config)
    assert "n1" in frontier
    assert "n2" in frontier
    assert "n3" not in frontier


def test_hysteresis_keeps_current_winner():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 99.5}),
    ]
    from simpleevo.db.store import FrontierAxis
    current = [FrontierAxis("total_ms", "n1", 100.0, 0.0, None, 0.0)]
    config = FrontierConfig(axes=("total_ms",), tie_band=0.0, hysteresis_margin=1.0)
    frontier = compute_frontier(nodes, current, config)
    # n2 is better but not by > hysteresis_margin, so n1 stays.
    assert "n1" in frontier


def test_hysteresis_overridden_by_big_beat():
    nodes = [
        _node("n1", {"total_ms": 100.0}),
        _node("n2", {"total_ms": 90.0}),
    ]
    from simpleevo.db.store import FrontierAxis
    current = [FrontierAxis("total_ms", "n1", 100.0, 0.0, None, 0.0)]
    config = FrontierConfig(axes=("total_ms",), tie_band=0.0, hysteresis_margin=1.0)
    frontier = compute_frontier(nodes, current, config)
    assert "n2" in frontier
    assert "n1" not in frontier


def test_sample_weighted_by_axis_count():
    class FakeFrontier:
        node_ids = frozenset({"a", "b"})

        def axis_count(self, node_id: str) -> int:
            return {"a": 2, "b": 1}[node_id]

    allocations: dict[str, int] = {}
    samples = sample_proposer_nodes(FakeFrontier(), allocations, 100)
    a_count = sum(1 for s in samples if s == "a")
    b_count = sum(1 for s in samples if s == "b")
    assert a_count > b_count
