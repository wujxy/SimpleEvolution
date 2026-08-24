"""Read-only query projections over the L2 Research DB.

These queries are used by the Scheduler and the Proposer; all mutations remain
in store.py.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .store import (
    Epoch, Episode, Experiment, Node, Proposal, ProposerAllocation,
    SupervisorEvent,
    _episode_from_row, _epoch_from_row, _experiment_from_row,
    _node_from_row, _proposal_from_row,
    _proposer_allocation_from_row, _research_state_from_row,
)
from simpleevo.research_state import ResearchState


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

    def proposal_count_for_node(self, node_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM proposals WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            return int(row["n"])

    def running_attempt_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS n FROM attempts "
                "WHERE status = 'running' GROUP BY kind"
            ).fetchall()
            return {row["kind"]: int(row["n"]) for row in rows}

    def queued_proposal_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM proposals WHERE status = 'queued'"
            ).fetchone()
            return int(row["n"])

    def open_allocation_counts_by_node(self) -> dict[str, int]:
        """Open (in-flight) leases per node — seats in flight."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT node_id, COUNT(*) AS n FROM proposer_allocations "
                "WHERE finished_at IS NULL GROUP BY node_id"
            ).fetchall()
            return {row["node_id"]: int(row["n"]) for row in rows}

    def open_allocation_node_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT node_id FROM proposer_allocations "
                "WHERE finished_at IS NULL"
            ).fetchall()
            return {row["node_id"] for row in rows}

    def open_allocation_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM proposer_allocations "
                "WHERE finished_at IS NULL"
            ).fetchone()
            return int(row["n"])

    # ------------------------------------------------------------------
    # Lease state machine reads (complete research; single implementation
    # shared by the scheduler's capacity enforcement and the supervisor's
    # facts — capacity and facts must not fork)
    # ------------------------------------------------------------------

    def researching_open_allocation_count(self) -> int:
        """Open leases actively holding a seat (NULL state = researching).

        A lease parked in awaiting_adjudication or reopen does NOT consume
        proposer capacity: its adjudication experiment consumes experiment
        capacity instead, and a closed-out seat must not block the next
        purchase.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM proposer_allocations "
                "WHERE finished_at IS NULL "
                "AND COALESCE(state, 'researching') = 'researching'"
            ).fetchone()
            return int(row["n"])

    def _allocations_in_state(self, state: str) -> list[ProposerAllocation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM proposer_allocations "
                "WHERE finished_at IS NULL AND COALESCE(state,'researching') = ?",
                (state,),
            ).fetchall()
            return [_proposer_allocation_from_row(row) for row in rows]

    def awaiting_adjudication_allocations(self) -> list[ProposerAllocation]:
        return self._allocations_in_state("awaiting_adjudication")

    def reopen_allocations(self) -> list[ProposerAllocation]:
        return self._allocations_in_state("reopen")

    def open_allocation_for_episode(
        self, episode_id: str,
    ) -> ProposerAllocation | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM proposer_allocations "
                "WHERE episode_id = ? AND finished_at IS NULL",
                (episode_id,),
            ).fetchone()
            return None if row is None else _proposer_allocation_from_row(row)

    def lease_adjudication_for_episode(self, episode_id: str) -> dict | None:
        """The latest adjudication write-back for a lease's episode.

        The reopened seat reads this at wake (the supervisor's
        previous_rejection pattern): what was rejected, which gates failed,
        and which world SHA it came from.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM scheduler_events "
                "WHERE type = 'lease_adjudication' "
                "AND json_extract(payload, '$.episode_id') = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def lease_conclusion_rejection_count(self, allocation_id: str) -> int:
        """How often a lease's conclusion was rejected by the ingest guard.

        The generalized vacuous-exit bound keys on these events, not on
        attempt counts — reopens legitimately add attempts.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM scheduler_events "
                "WHERE type = 'lease_conclusion_rejected' "
                "AND json_extract(payload, '$.allocation_id') = ?",
                (allocation_id,),
            ).fetchone()
            return int(row["n"])

    def research_state_head(self, episode_id: str) -> ResearchState | None:
        """A lease's current (revision-max) research state row."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_states WHERE episode_id = ? "
                "ORDER BY COALESCE(revision, 0) DESC, created_at DESC, "
                "rowid DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            return None if row is None else _research_state_from_row(row)

    def lease_wall_seconds(self, allocation_id: str) -> float:
        """Total proposer-attempt wall time consumed by a lease."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT SUM(COALESCE(finished_at, ?) - started_at) AS total "
                "FROM attempts WHERE logical_work_id = ? AND kind = 'proposer'",
                (time.time(), allocation_id),
            ).fetchone()
            return float(row["total"] or 0.0)

    def lease_attempt_ids(self, allocation_id: str) -> list[str]:
        """The attempt ids under one lease (usage-ledger attribution key)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT attempt_id FROM attempts "
                "WHERE logical_work_id = ? AND kind = 'proposer' "
                "ORDER BY created_at",
                (allocation_id,),
            ).fetchall()
            return [row["attempt_id"] for row in rows]

    def open_experiment_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM experiments "
                "WHERE status IN ('pending', 'running')"
            ).fetchone()
            return int(row["n"])

    def node_status_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM nodes GROUP BY status"
            ).fetchall()
            return {row["status"]: int(row["n"]) for row in rows}

    def terminal_experiment_count(self) -> int:
        """Scientific terminal evaluations (completed/gate_rejected/no_change).

        Infra failures land on attempts, not on ``experiments.status``, so
        they never consume eval budget.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM experiments "
                "WHERE status IN ('completed', 'gate_rejected', 'no_change')"
            ).fetchone()
            return int(row["n"])

    def run_limits(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, value FROM run_limits").fetchall()
            return {
                row["name"]: json.loads(row["value"]) for row in rows
            }

    def get_research_state(self, research_state_id: str) -> ResearchState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_states WHERE research_state_id = ?",
                (research_state_id,),
            ).fetchone()
            return None if row is None else _research_state_from_row(row)

    def research_states_for_episode(self, episode_id: str) -> list[ResearchState]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM research_states WHERE episode_id = ? "
                "ORDER BY created_at, research_state_id",
                (episode_id,),
            ).fetchall()
            return [_research_state_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Shared reads (single implementation: the Scheduler and the agent
    # workers both consume these — facts and enforcement must not fork)
    # ------------------------------------------------------------------

    def open_allocations(self) -> list[ProposerAllocation]:
        """Return proposer allocations that are still in flight."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM proposer_allocations WHERE finished_at IS NULL"
            ).fetchall()
            return [_proposer_allocation_from_row(row) for row in rows]

    def count_running_attempts(self, kind: str) -> int:
        """Count attempts currently marked running for a work kind."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM attempts "
                "WHERE kind = ? AND status = 'running'", (kind,),
            ).fetchone()
            return int(row["n"])

    def current_epoch(self) -> Epoch | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM epochs "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            return None if row is None else _epoch_from_row(row)

    def scheduler_rejection_for_work(self, work_id: str) -> str | None:
        """The most recent rejection error recorded for one logical work id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM scheduler_events "
                "WHERE type = 'supervisor_decision_rejected' "
                "AND json_extract(payload, '$.work_id') = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (work_id,),
            ).fetchone()
        return None if row is None else json.loads(row["payload"]).get("error")

    def supervisor_event_cursor(self, consumer: str = "supervisor") -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_consumed_event_id FROM supervisor_cursor "
                "WHERE consumer = ?", (consumer,),
            ).fetchone()
            return 0 if row is None else int(row[0])

    def pending_supervisor_events(self) -> list[SupervisorEvent]:
        cursor = self.supervisor_event_cursor()
        return self.supervisor_events_between(cursor, None)

    def supervisor_events_between(
        self, after_id: int, upto_id: int | None,
    ) -> list[SupervisorEvent]:
        """Events with ``after_id < event_id`` (and ``<= upto_id`` if given).

        The bounded read is what an agent worker uses to rebuild exactly the
        batch it was hired for, even if new events landed after submission.
        """
        sql = "SELECT * FROM supervisor_events WHERE event_id > ?"
        params: list[Any] = [after_id]
        if upto_id is not None:
            sql += " AND event_id <= ?"
            params.append(upto_id)
        sql += " ORDER BY event_id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                SupervisorEvent(
                    event_id=int(row["event_id"]),
                    type=row["type"],
                    payload=json.loads(row["payload"]),
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def burned_lenses(self) -> dict[str, set[str]]:
        """Lenses burned per node: its own episodes plus its whole ancestry.

        A lens stamped on an episode is a question this lineage has already
        bought; re-buying it downstream repeats the same bet (seat design
        §2.2).  Open seats count as burned — their episodes exist.
        """
        nodes = {
            node.node_id: node for node in self.list_nodes()
        }
        own: dict[str, set[str]] = {}
        for row in self.episode_operator_rows():
            own.setdefault(row["node_id"], set()).add(row["lens"])
        burned: dict[str, set[str]] = {}
        for node_id in nodes:
            tried: set[str] = set()
            current = node_id
            hops: set[str] = set()
            while current and current not in hops:
                hops.add(current)
                node = nodes.get(current)
                if node is None:
                    break
                tried.update(own.get(current, ()))
                current = node.parent_node_id
            burned[node_id] = tried
        return burned

    def research_state_width(self) -> list[dict[str, Any]]:
        """Return Node-local identity counts without interpreting state text."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH state_totals AS (
                    SELECT node_id, COUNT(*) AS registered_states
                    FROM research_states GROUP BY node_id
                ), proposal_totals AS (
                    SELECT node_id,
                           COUNT(*) AS total_proposals,
                           COUNT(DISTINCT research_state_id) AS proposed_states
                    FROM proposals GROUP BY node_id
                ), per_state AS (
                    SELECT node_id, research_state_id, COUNT(*) AS proposal_count
                    FROM proposals
                    WHERE research_state_id IS NOT NULL
                    GROUP BY node_id, research_state_id
                ), concentration AS (
                    SELECT node_id, MAX(proposal_count) AS max_proposals_per_state
                    FROM per_state GROUP BY node_id
                )
                SELECT n.node_id,
                       COALESCE(s.registered_states, 0) AS registered_states,
                       COALESCE(p.proposed_states, 0) AS proposed_states,
                       COALESCE(p.total_proposals, 0) AS total_proposals,
                       COALESCE(c.max_proposals_per_state, 0)
                           AS max_proposals_per_state
                FROM nodes n
                LEFT JOIN state_totals s ON s.node_id = n.node_id
                LEFT JOIN proposal_totals p ON p.node_id = n.node_id
                LEFT JOIN concentration c ON c.node_id = n.node_id
                ORDER BY n.created_at, n.node_id
                """
            ).fetchall()
            return [
                {
                    "node_id": row["node_id"],
                    "registered_states": row["registered_states"],
                    "proposed_states": row["proposed_states"],
                    "total_proposals": row["total_proposals"],
                    "max_proposals_per_state": row[
                        "max_proposals_per_state"
                    ],
                }
                for row in rows
            ]

    def episode_operator_rows(self) -> list[dict[str, Any]]:
        """Every episode carrying a lens, with its seat bookkeeping.

        One row per (node, episode): the lens id (``variation_operator``),
        whether the episode ever held a lease, whether one is open right
        now (and in which lease state), and how many proposals it produced.
        The seat ledger / lineage dedup / lens stats facts are all derived
        from this projection.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.node_id,
                       e.episode_id,
                       e.variation_operator,
                       e.conclusion_type,
                       (SELECT COUNT(*) FROM proposer_allocations a
                         WHERE a.episode_id = e.episode_id) AS leases,
                       (SELECT COUNT(*) FROM proposer_allocations a
                         WHERE a.episode_id = e.episode_id
                           AND a.finished_at IS NULL) AS open_leases,
                       (SELECT COALESCE(a.state, 'researching')
                          FROM proposer_allocations a
                         WHERE a.episode_id = e.episode_id
                           AND a.finished_at IS NULL
                         LIMIT 1) AS lease_state,
                       (SELECT COALESCE(MAX(a.reopen_count), 0)
                          FROM proposer_allocations a
                         WHERE a.episode_id = e.episode_id) AS reopen_count,
                       (SELECT COUNT(*) FROM proposals p
                         WHERE p.episode_id = e.episode_id) AS proposals
                FROM episodes e
                WHERE e.variation_operator IS NOT NULL
                ORDER BY e.created_at, e.episode_id
                """
            ).fetchall()
            return [
                {
                    "node_id": row["node_id"],
                    "episode_id": row["episode_id"],
                    "lens": row["variation_operator"],
                    "leases": int(row["leases"]),
                    "open_leases": int(row["open_leases"]),
                    "lease_state": row["lease_state"],
                    "reopen_count": int(row["reopen_count"]),
                    "proposals": int(row["proposals"]),
                    "conclusion_type": row["conclusion_type"],
                }
                for row in rows
            ]

    def proposal_outcome_rows(self) -> list[dict[str, Any]]:
        """Every proposal with its experiment outcome (if any yet).

        Carries the seat attribution chain the lens stats need:
        proposal → episode → lens, plus the experiment's measured outcome
        against its parent node.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.proposal_id,
                       p.node_id,
                       p.episode_id,
                       e.variation_operator,
                       x.experiment_id,
                       x.status,
                       x.gate_result,
                       x.metrics,
                       x.parent_node_id
                FROM proposals p
                LEFT JOIN episodes e ON e.episode_id = p.episode_id
                LEFT JOIN experiments x ON x.proposal_id = p.proposal_id
                ORDER BY p.created_at, p.proposal_id
                """
            ).fetchall()
            return [
                {
                    "proposal_id": row["proposal_id"],
                    "node_id": row["node_id"],
                    "episode_id": row["episode_id"],
                    "lens": row["variation_operator"],
                    "experiment_id": row["experiment_id"],
                    "status": row["status"],
                    "gate_result": json.loads(row["gate_result"] or "{}"),
                    "metrics": json.loads(row["metrics"] or "{}"),
                    "parent_node_id": row["parent_node_id"],
                }
                for row in rows
            ]

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
                       finished_at, proposals_produced, decision_id,
                       reserved_proposal_ids
                FROM proposer_allocations
                WHERE node_id = ?
                ORDER BY started_at
                """,
                (node_id,),
            ).fetchall()
            allocations = []
            for row in rows:
                item = dict(row)
                item["reserved_proposal_count"] = len(
                    json.loads(item.pop("reserved_proposal_ids") or "[]")
                )
                allocations.append(item)
            return allocations
