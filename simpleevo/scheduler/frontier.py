"""Pluggable frontier policies for the scheduler.

A ``FrontierPolicy`` decides two things: which active nodes win a place on the
resource-allocation frontier (``compute``), and how proposer slots are drawn
from that frontier (``sample``).  The scheduler, the store's persistence path,
and the reporting replay all go through the thin ``compute_frontier`` /
``sample_proposer_nodes`` facades, so adding a policy means adding one class in
this module (plus a config resolver) — nothing downstream needs to know its
parameters.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from simpleevo.db.store import FrontierAxis, Node


class FrontierPolicy(Protocol):
    """A frontier selection policy: computes winners and samples proposers."""

    def compute(self, nodes: Iterable[Node], config: "FrontierConfig") -> "Frontier":
        """Return the frontier (winner view) over ``nodes``."""
        ...

    def sample(
        self,
        frontier: "Frontier",
        allocations: dict[str, int],
        capacity: int,
        *,
        random_seed: int | None = None,
    ) -> list[str]:
        """Draw up to ``capacity`` proposer slots from ``frontier``.

        ``allocations`` is a mutable counter used to spread capacity across
        the frontier over repeated calls.
        """
        ...


@dataclass(frozen=True)
class FrontierConfig:
    axes: tuple[str, ...]
    policy: FrontierPolicy = field(default_factory=lambda: GepaPolicy())
    schema: dict[str, Any] | None = None


class Frontier:
    """Computed resource-allocation view over active nodes."""

    def __init__(self, node_ids: set[str], axes: dict[str, set[str]]):
        self.node_ids = frozenset(node_ids)
        self.axes = {axis: frozenset(nodes) for axis, nodes in axes.items()}

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.node_ids

    def __iter__(self):
        return iter(self.node_ids)

    def axis_count(self, node_id: str) -> int:
        return sum(1 for nodes in self.axes.values() if node_id in nodes)


def _axis_direction(axis: str, schema: dict[str, Any] | None) -> bool:
    """Return True if lower is better for the axis.

    Resolves from ``metrics_schema`` in one of two forms, matching every other
    direction reader in the codebase (reporting, ablation, proposer):
      1. ``metrics_schema.objective`` block — the canonical declaration. If
         ``objective.key == axis``, use ``objective.lower_is_better``.
      2. Per-axis key ``metrics_schema[axis].lower_is_better`` — the legacy /
         test form used by the scheduler unit tests.
    The old code read only form 2 and defaulted to True, so any higher-better
    objective declared in the ``objective`` block (e.g. xsbench lookups_per_sec)
    was treated as lower-better and the frontier kept the WORST nodes as winners.
    """
    if schema is None:
        return True
    objective = schema.get("objective") or {}
    if objective.get("key") == axis:
        return bool(objective.get("lower_is_better", True))
    axis_schema = schema.get(axis, {})
    return bool(axis_schema.get("lower_is_better", True))


def compute_frontier(
    nodes: Iterable[Node],
    current_axes: Iterable[FrontierAxis],
    config: FrontierConfig,
    *,
    random_seed: int | None = None,
) -> Frontier:
    """Compute the frontier from active nodes via the configured policy."""
    frontier = config.policy.compute(nodes, config)

    # Bootstrap: if no measured axis has a winner yet, allow fresh scientists
    # to study active root nodes so the tree can start growing.
    if not frontier.node_ids:
        active = [n for n in nodes if n.status == "active"]
        root_nodes = {n.node_id for n in active if n.depth == 0}
        frontier = Frontier(root_nodes, {})

    return frontier


def sample_proposer_nodes(
    frontier: Frontier,
    allocations: dict[str, int],
    capacity: int,
    config: FrontierConfig,
    *,
    random_seed: int | None = None,
) -> list[str]:
    """Sample up to ``capacity`` proposer slots via the configured policy."""
    return config.policy.sample(
        frontier, allocations, capacity, random_seed=random_seed
    )


def build_policy(name: str, *, top_k: int = 3) -> FrontierPolicy:
    """Resolve a frontier policy by name (from task config)."""
    if name == "topk":
        return TopKPolicy(k=top_k)
    return GepaPolicy()


def _metric_values(
    nodes: Iterable[Node],
    axes: tuple[str, ...],
) -> dict[str, dict[str, float]]:
    """Map node_id -> {axis: finite numeric value}, restricted to ``axes``."""
    values: dict[str, dict[str, float]] = {}
    for n in nodes:
        node_vals: dict[str, float] = {}
        metrics = n.metrics or {}
        for axis in axes:
            v = metrics.get(axis)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                node_vals[axis] = float(v)
        if node_vals:
            values[n.node_id] = node_vals
    return values


def _frequency_weighted_sample(
    frontier: Frontier,
    allocations: dict[str, int],
    capacity: int,
    *,
    random_seed: int | None = None,
) -> list[str]:
    """Sample weighted by axis count, discounted by past allocations.

    Weighting by axis count favours multi-axis winners; the ``(1 + 0.5*past)``
    discount prevents starvation over repeated calls.  May return duplicates
    when ``capacity`` exceeds |Frontier|.
    """
    rng = random.Random(random_seed)
    if not frontier.node_ids:
        return []

    weights: dict[str, float] = {}
    for node_id in frontier.node_ids:
        f = frontier.axis_count(node_id)
        past = allocations.get(node_id, 0)
        weights[node_id] = max(0.1, f / (1.0 + 0.5 * past))

    total = sum(weights.values())
    if total <= 0:
        return []

    sampled: list[str] = []
    for _ in range(capacity):
        pick = rng.choices(
            population=list(weights.keys()),
            weights=list(weights.values()),
            k=1,
        )[0]
        sampled.append(pick)
        allocations[pick] = allocations.get(pick, 0) + 1
    return sampled


class GepaPolicy:
    """GEPA frontier (arXiv:2507.19457): per-axis best + dominated prune.

    For each axis the winner set is every active node achieving the best
    (direction-aware) value on that axis — statistical ties are all retained.
    The union across axes is then pruned: a node is removed when another node
    is no worse on every axis it is measured on and strictly better on at
    least one. A node missing a value on an axis another node has cannot claim
    dominance, so the measured node is conservatively kept. No hysteresis /
    tie-band.
    """

    def compute(self, nodes: Iterable[Node], config: FrontierConfig) -> Frontier:
        active = [n for n in nodes if n.status == "active"]
        values = _metric_values(active, config.axes)

        axes_winners: dict[str, set[str]] = {}
        for axis in config.axes:
            lower = _axis_direction(axis, config.schema)
            best: float | None = None
            winners: set[str] = set()
            for nid, node_vals in values.items():
                v = node_vals.get(axis)
                if v is None:
                    continue
                if best is None or (v < best if lower else v > best):
                    best, winners = v, {nid}
                elif v == best:
                    winners.add(nid)
            if winners:
                axes_winners[axis] = winners

        union: set[str] = set()
        for w in axes_winners.values():
            union.update(w)

        def dominates(u: str, v: str) -> bool:
            u_vals, v_vals = values[u], values[v]
            strictly = False
            for axis in v_vals:  # only axes where v is measured
                if axis not in u_vals:
                    return False
                if _axis_direction(axis, config.schema):
                    if u_vals[axis] > v_vals[axis]:
                        return False
                    if u_vals[axis] < v_vals[axis]:
                        strictly = True
                else:
                    if u_vals[axis] < v_vals[axis]:
                        return False
                    if u_vals[axis] > v_vals[axis]:
                        strictly = True
            return strictly

        non_dominated = {
            nid for nid in union
            if not any(dominates(other, nid) for other in union if other != nid)
        }

        # Keep the axes map consistent with the surviving frontier (the
        # persisted rows are written from frontier.axes, so pruned nodes must
        # not linger there).
        axes_winners = {
            axis: (w & non_dominated)
            for axis, w in axes_winners.items()
            if (w & non_dominated)
        }
        return Frontier(non_dominated, axes_winners)

    def sample(
        self,
        frontier: Frontier,
        allocations: dict[str, int],
        capacity: int,
        *,
        random_seed: int | None = None,
    ) -> list[str]:
        return _frequency_weighted_sample(
            frontier, allocations, capacity, random_seed=random_seed
        )


class TopKPolicy:
    """Top-K frontier: the ``k`` best active nodes per axis, unioned.

    Simple breadth policy: any node in the top ``k`` on an axis keeps its place
    on the frontier (no dominated pruning), so up to ``k`` lineages evolve in
    parallel while enough distinct measured nodes exist.
    """

    def __init__(self, k: int = 3):
        self.k = max(1, int(k))

    def compute(self, nodes: Iterable[Node], config: FrontierConfig) -> Frontier:
        active = [n for n in nodes if n.status == "active"]
        values = _metric_values(active, config.axes)
        axes_winners: dict[str, set[str]] = {}
        for axis in config.axes:
            lower = _axis_direction(axis, config.schema)
            entries = [
                (nid, v)
                for nid, node_vals in values.items()
                if (v := node_vals.get(axis)) is not None
            ]
            if not entries:
                continue
            entries.sort(key=lambda t: (t[1], t[0]))  # value asc, id tiebreak
            if not lower:
                entries = entries[::-1]                # higher-better -> take top
            axes_winners[axis] = {nid for nid, _ in entries[: self.k]}

        frontier_nodes: set[str] = set()
        for w in axes_winners.values():
            frontier_nodes.update(w)
        return Frontier(frontier_nodes, axes_winners)

    def sample(
        self,
        frontier: Frontier,
        allocations: dict[str, int],
        capacity: int,
        *,
        random_seed: int | None = None,
    ) -> list[str]:
        return _frequency_weighted_sample(
            frontier, allocations, capacity, random_seed=random_seed
        )
