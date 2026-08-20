"""Data projection for SimpleEvolution visualisation.

Reads the run's ``task.yaml`` (objective key + axis directions) and the SQLite
research DB, projects them into a single ``TreeView`` consumed by every
renderer, and computes the quality series the plots need:

- ``best_so_far`` — best objective vs the unified experiment ordinal (every
  submitted experiment consumes one x-slot, gate-rejected / no-change included).
- ``experiment_marks`` — gate-rejected / no-change experiments for the × layer.
- ``improvement_series`` / ``improvement_multiple_series`` — per-axis
  best-so-far vs the measured root baseline, as % or × multiple (the % view
  falls back to absolute value when the root was never measured; the × view is
  omitted without a baseline).

Read-only: never mutates the run directory or the database.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simpleevo.config import load_config
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import Node


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
    experiment_idx: int | None  # 1-based ordinal over ALL experiments (None = root/orphan)
    frontier_axes: frozenset[str]


@dataclass(frozen=True)
class ExperimentView:
    """One experiment row flattened for the ordinal x-axis.

    Gate-rejected / no-change experiments create no node and carry no objective
    of their own; ``parent_objective`` is the parent NODE's objective so a
    rejection can be drawn as a × at the parent's y.
    """

    exp_idx: int  # 1-based ordinal over ALL experiments (created_at order)
    experiment_id: str
    status: str  # pending | running | completed | gate_rejected | no_change
    parent_objective: float | None
    created_at: float
    parent_node_id: str
    child_node_id: str | None
    gate_result: Any


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
    experiments: tuple[ExperimentView, ...] = field(default_factory=tuple)
    root_objective: Mapping[str, float | None] = field(default_factory=dict)


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

    # Unified experiment ordinal: every submitted experiment consumes one x-slot
    # (gate-rejected / no-change included), numbered in submission order so a
    # run's figure is stable across re-renders.  A node inherits the ordinal of
    # the experiment that created it; root/orphan nodes get None.
    # ``list_experiments`` has no ORDER BY, so sort here (id tie-breaks ticks).
    experiments = sorted(
        queries.list_experiments(),
        key=lambda e: (e.created_at, e.experiment_id),
    )
    exp_ordinal = {e.experiment_id: i + 1 for i, e in enumerate(experiments)}
    node_objective = {
        n.node_id: _as_float((n.metrics or {}).get(objective_key))
        for n in nodes
    }
    experiment_idx: dict[str, int | None] = {
        n.node_id: (
            None if n.experiment_id is None else exp_ordinal.get(n.experiment_id)
        )
        for n in nodes
    }
    experiment_views = tuple(
        ExperimentView(
            exp_idx=i + 1,
            experiment_id=e.experiment_id,
            status=e.status,
            parent_objective=node_objective.get(e.parent_node_id),
            created_at=e.created_at,
            parent_node_id=e.parent_node_id,
            child_node_id=e.child_node_id,
            gate_result=e.gate_result,
        )
        for i, e in enumerate(experiments)
    )
    root = next((n for n in nodes if n.parent_node_id is None), None)
    root_objective = {
        ax: _as_float((root.metrics or {}).get(ax)) if root else None
        for ax in axes
    }

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
        experiments=experiment_views,
        root_objective=root_objective,
    )


def best_so_far(view: TreeView) -> list[tuple[int, float]]:
    """Best objective seen after each passed node.

    Only gate-passed nodes are worlds; dead nodes never enter the series.
    Iterates in ``experiment_idx`` order (not ``created_at``) so the envelope
    stays monotonic on the unified experiment x-axis even when completions land
    out of submission order (``max_experiment_inflight > 1``).
    Returns [(experiment_ordinal, best_value)].
    """
    points: list[tuple[int, float]] = []
    best: float | None = None
    for v in sorted(
        (v for v in view.nodes if v.experiment_idx is not None),
        key=lambda v: v.experiment_idx,
    ):
        if not v.passed or v.objective is None:
            continue
        if best is None or (
            v.objective < best if view.lower_is_better else v.objective > best
        ):
            best = v.objective
        points.append((v.experiment_idx, best))
    return points


def experiment_marks(view: TreeView) -> list[tuple[int, float | None, str]]:
    """Rejected / no-change experiments as (exp_idx, parent_objective, status).

    These experiments never created a node, so they carry no objective of their
    own; the marker is drawn at the parent node's objective (None when the
    parent was never measured, e.g. the root).  Pending/running rows are not
    marks — they only occupy an empty x-slot.
    """
    return [
        (e.exp_idx, e.parent_objective, e.status)
        for e in view.experiments
        if e.status in ("gate_rejected", "no_change")
    ]


def _axis_lower(axis: str, schema: Mapping[str, Any]) -> bool:
    """Per-axis direction, mirroring ``frontier._axis_direction`` exactly so the
    %-sign matches the frontier replay's notion of "better"."""
    axis_schema = (schema or {}).get(axis, {})
    return bool(axis_schema.get("lower_is_better", True))


def _pct_change(
    root: float | None, value: float, lower_is_better: bool,
) -> float | None:
    """Root-relative % change, signed so improvement is always positive.

    Returns None when the root baseline is missing, zero, or non-finite (a %
    against an unknown baseline would be fabricated).
    """
    if root is None or root == 0 or not math.isfinite(root):
        return None
    delta = (root - value) if lower_is_better else (value - root)
    return delta / abs(root) * 100.0


def axis_best_so_far(view: TreeView) -> dict[str, list[tuple[int, float]]]:
    """Per-axis monotonic best value vs experiment ordinal (absolute units).

    Best-so-far is used instead of the frontier winner set because a top-k
    frontier keeps dominated candidates resident, and plotting those as a
    "winner" step misreads as regression: best-so-far only ever improves.
    """
    raw_by_id = {n.node_id: n for n in view.raw_nodes}
    series: dict[str, list[tuple[int, float]]] = {ax: [] for ax in view.axes}
    bests: dict[str, float] = {}
    for v in sorted(
        (v for v in view.nodes if v.experiment_idx is not None),
        key=lambda v: v.experiment_idx,
    ):
        if not v.passed:
            continue
        raw = raw_by_id.get(v.node_id)
        if raw is None:
            continue
        for ax in view.axes:
            value = _as_float((raw.metrics or {}).get(ax))
            if value is None:
                continue
            lower = _axis_lower(ax, view.metrics_schema)
            if ax not in bests or (
                value < bests[ax] if lower else value > bests[ax]
            ):
                bests[ax] = value
            series[ax].append((v.experiment_idx, bests[ax]))
    return series


def improvement_series(view: TreeView) -> dict[str, list[tuple[int, float]]]:
    """Per-axis best-so-far series, y = % vs the measured root baseline.

    Converted to % improvement over the measured root baseline
    (``_pct_change``); when the root was never measured (legacy runs) it falls
    back to the raw value.
    """
    series: dict[str, list[tuple[int, float]]] = {}
    for ax, points in axis_best_so_far(view).items():
        lower = _axis_lower(ax, view.metrics_schema)
        root = view.root_objective.get(ax)
        out: list[tuple[int, float]] = []
        for idx, best in points:
            pct = _pct_change(root, best, lower)
            out.append((idx, pct if pct is not None else best))
        series[ax] = out
    return series


def _multiple_change(
    root: float | None, value: float, lower_is_better: bool,
) -> float | None:
    """× multiple vs baseline; >1 = better than baseline. None when the
    baseline or value is missing/zero/non-finite (a ratio would be fabricated)."""
    if (
        root is None
        or root == 0
        or not math.isfinite(root)
        or not math.isfinite(value)
        or value == 0
    ):
        return None
    return (root / value) if lower_is_better else (value / root)


def improvement_multiple_series(
    view: TreeView,
) -> dict[str, list[tuple[int, float]]]:
    """Per-axis best-so-far series, y = × multiple vs the measured root
    baseline (readable when a % gain saturates, e.g. 100-1000× speedups).

    Axes with no valid root baseline are omitted — the × view only exists in
    comparison to a real baseline.
    """
    series: dict[str, list[tuple[int, float]]] = {}
    for ax, points in axis_best_so_far(view).items():
        lower = _axis_lower(ax, view.metrics_schema)
        root = view.root_objective.get(ax)
        out: list[tuple[int, float]] = []
        for idx, best in points:
            multiple = _multiple_change(root, best, lower)
            if multiple is not None:
                out.append((idx, multiple))
        if out:
            series[ax] = out
    return series


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
