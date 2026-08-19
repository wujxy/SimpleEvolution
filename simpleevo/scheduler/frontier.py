"""GEPA-style frontier computation: per-axis winners with tie/hysteresis."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable

from simpleevo.db.store import FrontierAxis, Node


@dataclass(frozen=True)
class FrontierConfig:
    axes: tuple[str, ...]
    tie_band: float = 0.01
    hysteresis_margin: float = 0.01
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
    """Return True if lower is better for the axis."""
    if schema is None:
        return True
    axis_schema = schema.get(axis, {})
    return axis_schema.get("lower_is_better", True)


def compute_frontier(
    nodes: Iterable[Node],
    current_axes: Iterable[FrontierAxis],
    config: FrontierConfig,
    *,
    random_seed: int | None = None,
) -> Frontier:
    """Compute the new frontier from active nodes and current axis winners.

    For each axis:
      1. Find the best value among active nodes.
      2. Challengers are nodes within ``tie_band`` of the best value.
      3. If current axis winners exist and no challenger beats them by more
         than ``hysteresis_margin``, keep the current winners.
      4. Otherwise, replace with challengers.

    The returned Frontier is the union of per-axis winner sets.
    """
    rng = random.Random(random_seed)
    active = [n for n in nodes if n.status == "active"]
    current_by_axis: dict[str, set[str]] = {}
    for ax in current_axes:
        current_by_axis.setdefault(ax.axis, set()).add(ax.node_id)

    axes_winners: dict[str, set[str]] = {}
    for axis in config.axes:
        lower_is_better = _axis_direction(axis, config.schema)
        values = [
            (n, n.metrics.get(axis))
            for n in active
            if isinstance(n.metrics.get(axis), (int, float))
            and not isinstance(n.metrics.get(axis), bool)
            and math.isfinite(n.metrics.get(axis))
        ]
        if not values:
            continue

        if lower_is_better:
            best = min(v for _, v in values)
            challengers = {
                n.node_id for n, v in values
                if v <= best + config.tie_band
            }
        else:
            best = max(v for _, v in values)
            challengers = {
                n.node_id for n, v in values
                if v >= best - config.tie_band
            }

        current_winners = current_by_axis.get(axis, set()) & {n.node_id for n, _ in values}
        if current_winners:
            dethroned = False
            for winner_id in current_winners:
                winner_val = next(v for n, v in values if n.node_id == winner_id)
                for challenger_id in challengers:
                    challenger_val = next(
                        v for n, v in values if n.node_id == challenger_id
                    )
                    if lower_is_better:
                        if challenger_val < winner_val - config.hysteresis_margin:
                            dethroned = True
                            break
                    else:
                        if challenger_val > winner_val + config.hysteresis_margin:
                            dethroned = True
                            break
                if dethroned:
                    break
            axes_winners[axis] = (
                current_winners if not dethroned else challengers
            )
        else:
            axes_winners[axis] = challengers

    frontier_nodes: set[str] = set()
    for winner_set in axes_winners.values():
        frontier_nodes.update(winner_set)

    # Bootstrap: if no measured axis has a winner yet, allow fresh scientists
    # to study active root nodes so the tree can start growing.
    if not frontier_nodes:
        root_nodes = {n.node_id for n in active if n.depth == 0}
        frontier_nodes.update(root_nodes)

    return Frontier(frontier_nodes, axes_winners)


def sample_proposer_nodes(
    frontier: Frontier,
    allocations: dict[str, int],
    capacity: int,
    *,
    random_seed: int | None = None,
) -> list[str]:
    """Sample up to ``capacity`` nodes weighted by f[node] (axis count).

    ``allocations`` is a mutable counter used to spread capacity across the
    frontier over repeated calls.  The returned list may contain duplicates
    when capacity exceeds |Frontier|, allowing the same node to receive more
    than one proposer slot.
    """
    rng = random.Random(random_seed)
    if not frontier.node_ids:
        return []

    weights: dict[str, float] = {}
    for node_id in frontier.node_ids:
        f = frontier.axis_count(node_id)
        past = allocations.get(node_id, 0)
        # Weight by axis count, discounted by how many times this node has
        # already been allocated recently.  This prevents starvation while
        # still favouring multi-axis winners.
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
