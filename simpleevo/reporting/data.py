"""Data projection for SimpleEvolution visualisation.

Reads the run's ``task.yaml`` (objective key + axis directions) and the SQLite
research DB, projects them into a single ``TreeView`` consumed by every
renderer, and computes the quality series (best-so-far, per-axis winner
history) that the plots need.

Read-only: never mutates the run directory or the database.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from simpleevo.config import load_config
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import FrontierAxis, Node
from simpleevo.scheduler.frontier import (
    FrontierConfig,
    build_policy,
    compute_frontier,
)


@dataclass(frozen=True)
class NodeView:
    """One research-tree node flattened for rendering."""

    node_id: str
    parent_node_id: str | None
    sha: str
    depth: int
    status: str  # active | dormant | dead
    objective: float | None
    passed: bool
    created_at: float
    experiment_idx: int | None  # 1-based completed-experiment ordinal (None = root)
    frontier_axes: frozenset[str]


@dataclass(frozen=True)
class TreeView:
    """Everything a renderer needs, projected once from disk."""

    objective_key: str
    lower_is_better: bool
    axes: tuple[str, ...]
    metrics_schema: Mapping[str, Any]
    pricing: Mapping[str, Any]
    nodes: tuple[NodeView, ...]  # sorted by created_at
    by_id: Mapping[str, NodeView]
    children: Mapping[str, tuple[str, ...]]  # parent -> child ids (created_at order)
    current_frontier: Mapping[str, frozenset[str]]  # axis -> current winner ids
    raw_nodes: tuple[Node, ...]  # for winner-history replay
    frontier_policy: str = "gepa"
    frontier_top_k: int = 3


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _load_frontier_axes(db_path: Path) -> list[tuple[str, str, float]]:
    """Read the current frontier snapshot (axis, node_id, value)."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT axis, node_id, value FROM frontier_axes"
        ).fetchall()
        return [(r[0], r[1], float(r[2])) for r in rows]
    finally:
        conn.close()


def load_tree_view(run_dir: str | Path) -> TreeView:
    """Project the run's research state into a render-ready TreeView."""
    run_dir = Path(run_dir)
    cfg_path = run_dir / "task.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"{cfg_path} missing; run `init`/`run --config` first"
        )
    cfg = load_config(cfg_path)
    schema = dict(cfg.metrics_schema)
    objective = schema.get("objective") or {}
    objective_key = str(objective.get("key", "OBJECTIVE"))
    lower_is_better = bool(objective.get("lower_is_better", True))
    axes = tuple(cfg.axes)

    queries = ResearchQueries(run_dir / "simpleevo.db")
    nodes = sorted(queries.list_nodes(), key=lambda n: n.created_at)

    # Assign the completed-experiment ordinal: root has none, every other node
    # is one finished experiment, numbered in creation order.
    experiment_idx: dict[str, int | None] = {}
    counter = 0
    for n in nodes:
        if n.parent_node_id is None:
            experiment_idx[n.node_id] = None
        else:
            counter += 1
            experiment_idx[n.node_id] = counter

    # Current frontier membership, filtered to real axes (drop __bootstrap__).
    current_frontier: dict[str, set[str]] = {ax: set() for ax in axes}
    for axis, node_id, _value in _load_frontier_axes(run_dir / "simpleevo.db"):
        if axis in current_frontier:
            current_frontier[axis].add(node_id)

    views = tuple(
        NodeView(
            node_id=n.node_id,
            parent_node_id=n.parent_node_id,
            sha=n.sha,
            depth=n.depth,
            status=n.status,
            objective=_as_float(n.metrics.get(objective_key)),
            passed=bool(n.gate_result.passed),
            created_at=n.created_at,
            experiment_idx=experiment_idx[n.node_id],
            frontier_axes=frozenset(
                ax for ax, ids in current_frontier.items() if n.node_id in ids
            ),
        )
        for n in nodes
    )
    by_id = {v.node_id: v for v in views}
    children: dict[str, list[str]] = {v.node_id: [] for v in views}
    for v in views:
        if v.parent_node_id and v.parent_node_id in children:
            children[v.parent_node_id].append(v.node_id)

    return TreeView(
        objective_key=objective_key,
        lower_is_better=lower_is_better,
        axes=axes,
        metrics_schema=schema,
        pricing=dict(cfg.pricing),
        frontier_policy=cfg.frontier_policy,
        frontier_top_k=cfg.frontier_top_k,
        nodes=views,
        by_id=by_id,
        children={k: tuple(v) for k, v in children.items()},
        current_frontier={k: frozenset(v) for k, v in current_frontier.items()},
        raw_nodes=tuple(nodes),
    )


def best_so_far(view: TreeView) -> list[tuple[int, float]]:
    """Best objective seen after each completed experiment.

    Only gate-passed nodes are worlds; dead nodes never enter the series.
    Returns [(completed_experiments, best_value)] in creation order.
    """
    points: list[tuple[int, float]] = []
    best: float | None = None
    for v in view.nodes:
        if v.experiment_idx is None or not v.passed or v.objective is None:
            continue
        if best is None or (
            v.objective < best if view.lower_is_better else v.objective > best
        ):
            best = v.objective
        points.append((v.experiment_idx, best))
    return points


def winner_history(view: TreeView) -> dict[str, list[tuple[int, str, float]]]:
    """Reconstruct per-axis winner history by replaying the frontier.

    ``frontier_axes`` is a current snapshot (rewritten on every ingest), so the
    historical winner sequence must be replayed from the nodes themselves. The
    replay uses the same ``FrontierConfig`` shape as the scheduler (same
    policy/top_k), so it matches the live frontier exactly.
    """
    config = FrontierConfig(
        axes=view.axes,
        schema=dict(view.metrics_schema),
        policy=build_policy(view.frontier_policy, top_k=view.frontier_top_k),
    )
    active = [n for n in view.raw_nodes if n.gate_result.passed]

    history: dict[str, list[tuple[int, str, float]]] = {
        ax: [] for ax in view.axes
    }
    seen: dict[str, set[str]] = {ax: set() for ax in view.axes}
    current: list[FrontierAxis] = []

    for i in range(1, len(active) + 1):
        prefix = active[:i]
        frontier = compute_frontier(prefix, current, config)
        current = []
        for ax in view.axes:
            winners = frontier.axes.get(ax, set())
            if winners != seen[ax]:
                for nid in sorted(winners):
                    node = next(n for n in prefix if n.node_id == nid)
                    view_node = view.by_id.get(nid)
                    idx = view_node.experiment_idx if view_node else None
                    value = _as_float(node.metrics.get(ax))
                    if idx is not None and value is not None:
                        history[ax].append((idx, nid, value))
                seen[ax] = set(winners)
            for nid in winners:
                node = next(n for n in prefix if n.node_id == nid)
                value = _as_float(node.metrics.get(ax))
                current.append(FrontierAxis(
                    axis=ax,
                    node_id=nid,
                    value=value if value is not None else 0.0,
                    margin=0.0,
                    hysteresis_anchor=None,
                    since=node.created_at,
                ))
    return history


def _load_usage(run_dir: Path) -> list[dict[str, Any]]:
    """Read the append-only usage.jsonl into a list of records."""
    import json

    path = run_dir / "telemetry" / "usage.jsonl"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


_DEFAULT_PRICING = {
    "input_usd_per_1m": 1.0,
    "output_usd_per_1m": 3.0,
    "cache_read_usd_per_1m": 0.1,
}


def _cost_usd(tokens: Mapping[str, Any], pricing: Mapping[str, Any]) -> float:
    """Convert a flat token dict to USD using the configured per-1M prices."""
    input_p = float(pricing.get("input_usd_per_1m", _DEFAULT_PRICING["input_usd_per_1m"]))
    output_p = float(pricing.get("output_usd_per_1m", _DEFAULT_PRICING["output_usd_per_1m"]))
    cache_read_p = float(pricing.get("cache_read_usd_per_1m", _DEFAULT_PRICING["cache_read_usd_per_1m"]))
    cache_creation_p = float(pricing.get("cache_creation_usd_per_1m", input_p))
    return (
        int(tokens.get("input_tokens", 0)) * input_p
        + int(tokens.get("output_tokens", 0)) * output_p
        + int(tokens.get("cache_read_input_tokens", 0)) * cache_read_p
        + int(tokens.get("cache_creation_input_tokens", 0)) * cache_creation_p
    ) / 1_000_000.0


def budget_series(
    view: TreeView,
    run_dir: str | Path,
) -> dict[str, list[tuple[float, float]]]:
    """Cumulative spend (USD) vs best-so-far, per role and total.

    Returns three series keyed by "proposer" / "executor" / "total"; each is a
    list of (cumulative_cost_usd, best_so_far_objective) aligned to completed
    experiments in creation order.
    """
    usage = sorted(
        _load_usage(Path(run_dir)),
        key=lambda u: float(u.get("timestamp", 0.0)),
    )
    idx_created = {
        v.experiment_idx: v.created_at
        for v in view.nodes if v.experiment_idx is not None
    }
    best = dict(best_so_far(view))

    series: dict[str, list[tuple[float, float]]] = {
        "proposer": [], "executor": [], "total": [],
    }
    proposer_cost = 0.0
    executor_cost = 0.0
    ui = 0
    for idx in sorted(idx_created):
        created = idx_created[idx]
        while ui < len(usage) and usage[ui]["timestamp"] <= created:
            record = usage[ui]
            cost = _cost_usd(record, view.pricing)
            if record.get("role") == "proposer":
                proposer_cost += cost
            else:
                executor_cost += cost
            ui += 1
        b = best.get(idx)
        if b is not None:
            series["proposer"].append((proposer_cost, b))
            series["executor"].append((executor_cost, b))
            series["total"].append((proposer_cost + executor_cost, b))
    return series
