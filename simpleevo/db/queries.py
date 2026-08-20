"""Read-only query projections over the L2 Research DB.

These queries are used by the Scheduler and the Proposer; all mutations remain
in store.py.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .store import (
    Episode, Experiment, Node, Proposal,
    _episode_from_row, _experiment_from_row, _node_from_row, _proposal_from_row,
)


@dataclass(frozen=True)
class LineageNode:
    node: Node
    children: tuple[str, ...]


class ResearchQueries:
    """Read-only access to the research database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_node(self, node_id: str) -> Node | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            return None if row is None else _node_from_row(row)

    def get_episode(self, episode_id: str) -> Episode | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            return None if row is None else _episode_from_row(row)

    def episodes_for_node(self, node_id: str, limit: int = 1) -> list[Episode]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE node_id = ? "
                "ORDER BY last_active_at DESC LIMIT ?",
                (node_id, limit),
            ).fetchall()
            return [_episode_from_row(row) for row in rows]

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            return None if row is None else _experiment_from_row(row)

    def list_experiments(self, status: str | None = None) -> list[Experiment]:
        with self._connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM experiments").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experiments WHERE status = ?", (status,)
                ).fetchall()
            return [_experiment_from_row(row) for row in rows]

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
            return None if row is None else _proposal_from_row(row)

    def list_nodes(self, status: str | None = None) -> list[Node]:
        with self._connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM nodes ORDER BY created_at").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE status = ? ORDER BY created_at",
                    (status,),
                ).fetchall()
            return [_node_from_row(row) for row in rows]

    def list_active_nodes(self) -> list[Node]:
        return self.list_nodes(status="active")

    def root_node(self) -> Node | None:
        """The tree root: the single node with no parent (the pristine SHA)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM nodes WHERE parent_node_id IS NULL LIMIT 1"
            ).fetchone()
            return None if row is None else _node_from_row(row)

    def node_lineage(self, node_id: str) -> list[Node]:
        """Return root -> ... -> node path."""
        with self._connect() as conn:
            lineage: list[Node] = []
            current = node_id
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                row = conn.execute(
                    "SELECT * FROM nodes WHERE node_id = ?", (current,)
                ).fetchone()
                if row is None:
                    break
                lineage.append(_node_from_row(row))
                current = row["parent_node_id"]
            lineage.reverse()
            return lineage

    def tree(self) -> dict[str, LineageNode]:
        """Return all nodes indexed by id with child pointers."""
        with self._connect() as conn:
            nodes = {
                row["node_id"]: _node_from_row(row)
                for row in conn.execute("SELECT * FROM nodes").fetchall()
            }
            children: dict[str, list[str]] = {
                node_id: [] for node_id in nodes
            }
            for node_id, node in nodes.items():
                if node.parent_node_id:
                    children[node.parent_node_id].append(node_id)
            return {
                node_id: LineageNode(
                    node=nodes[node_id],
                    children=tuple(sorted(children[node_id])),
                )
                for node_id in nodes
            }

    def queued_proposals(
        self,
        node_ids: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Proposal]:
        with self._connect() as conn:
            params: list[Any] = []
            sql = "SELECT * FROM proposals WHERE status = 'queued'"
            if node_ids is not None:
                ids = tuple(node_ids)
                if not ids:
                    return []
                placeholders = ",".join("?" for _ in ids)
                sql += f" AND node_id IN ({placeholders})"
                params.extend(ids)
            sql += " ORDER BY created_at"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_proposal_from_row(row) for row in rows]

    def frontier_nodes(self) -> list[Node]:
        """Nodes that currently hold at least one axis."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT n.* FROM nodes n
                WHERE n.node_id IN (SELECT DISTINCT node_id FROM frontier_axes)
                ORDER BY n.created_at
                """
            ).fetchall()
            return [_node_from_row(row) for row in rows]

    def allocations_for_node(self, node_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT allocation_id, node_id, episode_id, started_at,
                       finished_at, proposals_produced
                FROM proposer_allocations
                WHERE node_id = ?
                ORDER BY started_at
                """,
                (node_id,),
            ).fetchall()
            return [dict(row) for row in rows]
