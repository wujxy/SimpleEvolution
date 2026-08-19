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
from simpleevo.scheduler.frontier import FrontierConfig, compute_frontier


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
    nodes: tuple[NodeView, ...]  # sorted by created_at
    by_id: Mapping[str, NodeView]
    children: Mapping[str, tuple[str, ...]]  # parent -> child ids (created_at order)
    current_frontier: Mapping[str, frozenset[str]]  # axis -> current winner ids
    raw_nodes: tuple[Node, ...]  # for winner-history replay


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
    replay uses the same ``FrontierConfig`` shape as the scheduler (default
    tie_band/hysteresis), so it matches the live frontier exactly.
    """
    config = FrontierConfig(axes=view.axes, schema=dict(view.metrics_schema))
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
                    margin=config.tie_band,
                    hysteresis_anchor=value if value is not None else 0.0,
                    since=node.created_at,
                ))
    return history
