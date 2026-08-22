"""Frontier health telemetry for SimpleEvolution.

Writes append-only JSONL files under ``run_dir/telemetry/``:

- frontier_size.jsonl: |Frontier| per step.
- lineage_axis_share.jsonl: how many axes each root-to-leaf path holds.
- proposer_allocation_distribution.jsonl: actual allocations per node.
- research_state_width.jsonl: identity-linked cognitive width per node.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simpleevo.db.queries import ResearchQueries


@dataclass(frozen=True)
class StepTelemetry:
    step: int
    timestamp: float
    frontier_size: int
    lineage_axis_shares: list[dict[str, Any]]
    allocation_distribution: list[dict[str, Any]]
    research_state_width: list[dict[str, Any]]


class TelemetryRecorder:
    """Record scheduler telemetry to JSONL files."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.telemetry_dir = self.run_dir / "telemetry"
        self.telemetry_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        path = self.telemetry_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()

    def record(
        self,
        *,
        step: int,
        frontier_size: int,
        queries: ResearchQueries,
    ) -> StepTelemetry:
        timestamp = time.time()
        lineage_shares = self._lineage_axis_shares(queries)
        allocation_dist = self._allocation_distribution(queries)
        research_state_width = queries.research_state_width()

        self._append(
            "frontier_size.jsonl",
            {"step": step, "timestamp": timestamp, "frontier_size": frontier_size},
        )
        for share in lineage_shares:
            self._append("lineage_axis_share.jsonl", {"step": step, "timestamp": timestamp, **share})
        for item in allocation_dist:
            self._append(
                "proposer_allocation_distribution.jsonl",
                {"step": step, "timestamp": timestamp, **item},
            )
        for item in research_state_width:
            self._append(
                "research_state_width.jsonl",
                {"step": step, "timestamp": timestamp, **item},
            )

        return StepTelemetry(
            step=step,
            timestamp=timestamp,
            frontier_size=frontier_size,
            lineage_axis_shares=lineage_shares,
            allocation_distribution=allocation_dist,
            research_state_width=research_state_width,
        )

    def _lineage_axis_shares(self, queries: ResearchQueries) -> list[dict[str, Any]]:
        """For each root-to-leaf path, count how many distinct axes it wins."""
        tree = queries.tree()
        axes_by_node: dict[str, set[str]] = {}
        with sqlite3_connect(queries.path) as conn:
            rows = conn.execute(
                "SELECT axis, node_id FROM frontier_axes"
            ).fetchall()
            for axis, node_id in rows:
                axes_by_node.setdefault(node_id, set()).add(axis)

        # Find roots.
        roots = [nid for nid, entry in tree.items() if entry.node.parent_node_id is None]
        results: list[dict[str, Any]] = []
        for root_id in roots:
            for leaf_id in _leaves(tree, root_id):
                path = _path_to_root(tree, leaf_id)
                won_axes: set[str] = set()
                for nid in path:
                    won_axes.update(axes_by_node.get(nid, set()))
                results.append({
                    "root_node_id": root_id,
                    "leaf_node_id": leaf_id,
                    "path_length": len(path),
                    "axes_won": len(won_axes),
                    "axis_names": sorted(won_axes),
                })
        return results

    def _allocation_distribution(self, queries: ResearchQueries) -> list[dict[str, Any]]:
        """Aggregate finished proposer allocations per node."""
        with sqlite3_connect(queries.path) as conn:
            rows = conn.execute(
                """
                SELECT node_id,
                       COUNT(*) as total_allocations,
                       SUM(proposals_produced) as total_proposals
                FROM proposer_allocations
                GROUP BY node_id
                """
            ).fetchall()
            return [
                {
                    "node_id": row["node_id"],
                    "total_allocations": row["total_allocations"],
                    "total_proposals": row["total_proposals"] or 0,
                }
                for row in rows
            ]


def sqlite3_connect(path: Path):
    """Tiny helper so telemetry queries do not need the full store connection."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def spend_usd(run_dir: Path, pricing: dict) -> float:
    """Total model spend for a run, from telemetry/usage.jsonl records.

    Shared by the driver (cap policy) and the growth gate's budget view so
    both compute the same number from the same token ledger.
    """
    path = Path(run_dir) / "telemetry" / "usage.jsonl"
    if not path.exists():
        return 0.0
    input_p = float(pricing.get("input_usd_per_1m", 0.0))
    output_p = float(pricing.get("output_usd_per_1m", 0.0))
    cache_read_p = float(pricing.get("cache_read_usd_per_1m", 0.0))
    cache_creation_p = float(pricing.get(
        "cache_creation_usd_per_1m", input_p))
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += (
            int(record.get("input_tokens", 0)) * input_p
            + int(record.get("output_tokens", 0)) * output_p
            + int(record.get("cache_read_input_tokens", 0)) * cache_read_p
            + int(record.get("cache_creation_input_tokens", 0))
            * cache_creation_p
        ) / 1_000_000.0
    return total


def _leaves(tree: dict[str, Any], root_id: str) -> list[str]:
    """Return all leaf node ids under root_id."""
    leaves: list[str] = []
    stack = [root_id]
    while stack:
        nid = stack.pop()
        children = tree[nid].children
        if not children:
            leaves.append(nid)
        else:
            stack.extend(children)
    return leaves


def _path_to_root(tree: dict[str, Any], node_id: str) -> list[str]:
    """Return node_id -> ... -> root, inclusive."""
    path: list[str] = []
    current = node_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        path.append(current)
        current = tree[current].node.parent_node_id
    return path
