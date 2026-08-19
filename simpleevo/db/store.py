"""Single writer for SimpleEvolution L2 Research State.

All mutations go through ResearchStore.  HTCondor jobs write durable artifacts;
the Scheduler ingests them here.  Resume = reconciliation against this store.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .schema import ResearchDBSchema


def _new_id() -> str:
    return uuid.uuid4().hex


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _unjson(text: str) -> Any:
    return json.loads(text)


@dataclass(frozen=True)
class GateResult:
    passed: bool | None
    detail: str = ""


@dataclass(frozen=True)
class GateDecision:
    results: dict[str, GateResult]
    passed: bool


@dataclass(frozen=True)
class Node:
    node_id: str
    parent_node_id: str | None
    experiment_id: str | None
    sha: str
    metrics: dict[str, Any]
    gate_result: GateDecision
    depth: int
    status: str
    created_at: float


@dataclass(frozen=True)
class Thread:
    thread_id: str
    parent_thread_id: str | None
    node_id: str
    snapshot_ref: str
    created_at: float
    last_active_at: float


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    node_id: str
    thread_id: str
    instruction: str
    rationale: dict[str, Any]
    status: str
    created_at: float


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    proposal_id: str
    parent_node_id: str
    result_sha: str | None
    metrics: dict[str, Any]
    gate_result: GateDecision
    status: str
    changed_paths: tuple[str, ...]
    child_node_id: str | None
    created_at: float


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    logical_work_id: str
    kind: str
    status: str
    trace_ref: str | None
    artifact_ref: str | None
    host: str | None
    started_at: float | None
    finished_at: float | None
    created_at: float


@dataclass(frozen=True)
class FrontierAxis:
    axis: str
    node_id: str
    value: float
    margin: float
    hysteresis_anchor: float | None
    since: float


@dataclass(frozen=True)
class ProposerAllocation:
    allocation_id: str
    node_id: str
    thread_id: str
    reserved_proposal_ids: tuple[str, ...]
    started_at: float
    finished_at: float | None
    proposals_produced: int


class ResearchStore:
    """SQLite-backed L2 store with a single-writer contract."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            ResearchDBSchema.apply(conn)
            conn.commit()

    @contextmanager
    def transaction(self):
        """Context manager yielding a Transaction object."""
        conn = self._connect()
        try:
            tx = _Transaction(conn)
            yield tx
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ingest_experiment_result(
        self,
        *,
        experiment_id: str,
        result_sha: str | None,
        metrics: dict[str, Any],
        gate_result: GateDecision,
        status: str,
        changed_paths: Iterable[str] = (),
        frontier_config: "FrontierConfig | None" = None,
    ) -> Node | None:
        """Atomically mark experiment complete and, if gate passed, create child node.

        If ``frontier_config`` is provided, the Frontier is recomputed and
        persisted inside the same transaction, satisfying the atomic-transition
        design requirement.

        Returns the new Node or None.
        """
        with self.transaction() as tx:
            exp = tx.get_experiment(experiment_id)
            if exp is None:
                raise ValueError(f"unknown experiment: {experiment_id}")
            if exp.status not in {"pending", "running"}:
                raise ValueError(
                    f"experiment {experiment_id} is already terminal: {exp.status}"
                )

            tx.update_experiment_result(
                experiment_id=experiment_id,
                result_sha=result_sha,
                metrics=metrics,
                gate_result=gate_result,
                status=status,
                changed_paths=changed_paths,
            )

            child: Node | None = None
            if gate_result.passed and result_sha is not None:
                parent = tx.get_node(exp.parent_node_id)
                if parent is None:
                    raise ValueError(
                        f"experiment {experiment_id} references missing parent node"
                    )
                child = tx.create_node(
                    parent_node_id=parent.node_id,
                    experiment_id=experiment_id,
                    sha=result_sha,
                    metrics=metrics,
                    gate_result=gate_result,
                    depth=parent.depth + 1,
                    status="active",
                )
                tx.link_experiment_child(experiment_id, child.node_id)

                # Fork the Scientist Thread, inheriting the parent thread's
                # final cognitive state (not a per-proposal snapshot).
                proposal = tx.get_proposal(exp.proposal_id)
                if proposal is not None:
                    parent_thread = tx.get_thread(proposal.thread_id)
                    tx.create_thread(
                        parent_thread_id=proposal.thread_id,
                        node_id=child.node_id,
                        snapshot_ref=parent_thread.snapshot_ref if parent_thread else "",
                    )

            if frontier_config is not None:
                from simpleevo.scheduler.frontier import compute_frontier

                now = time.time()
                nodes = tx.list_active_nodes()
                current_axes = tx.load_frontier_axes()
                frontier = compute_frontier(nodes, current_axes, frontier_config)
                axes: list[FrontierAxis] = []
                for axis, node_ids in frontier.axes.items():
                    for node_id in node_ids:
                        node = next(n for n in nodes if n.node_id == node_id)
                        value = float(node.metrics.get(axis, 0.0))
                        axes.append(
                            FrontierAxis(
                                axis=axis,
                                node_id=node_id,
                                value=value,
                                margin=frontier_config.tie_band,
                                hysteresis_anchor=value,
                                since=now,
                            )
                        )
                # Bootstrap: root nodes remain in frontier when no axis has data.
                if not frontier.node_ids:
                    for node in nodes:
                        if node.depth == 0:
                            axes.append(
                                FrontierAxis(
                                    axis="__bootstrap__",
                                    node_id=node.node_id,
                                    value=0.0,
                                    margin=0.0,
                                    hysteresis_anchor=None,
                                    since=now,
                                )
                            )
                tx.update_frontier_axes(axes)

            return child

    def publish_proposals(
        self,
        *,
        node_id: str,
        thread_id: str,
        proposals: Iterable[dict[str, Any]],
        reserved_proposal_ids: Iterable[str] | None = None,
        final_snapshot_ref: str | None = None,
    ) -> list[Proposal]:
        """Atomically publish a batch of fully-formed proposals.

        Each proposal must carry its own ``proposal_id`` (issued by the
        Scheduler when the proposer allocation was created, §2.4
        identity-first).  When ``reserved_proposal_ids`` is given, every
        incoming id is validated against it so a worker cannot mint ids.

        ``final_snapshot_ref`` is the ONE final cognitive state of the
        completed episode that produced this batch; it is recorded on the
        thread (not per-proposal) so forked child threads inherit the parent
        Scientist's final state.
        """
        reserved = set(reserved_proposal_ids) if reserved_proposal_ids is not None else None
        created: list[Proposal] = []
        now = time.time()
        with self.transaction() as tx:
            node = tx.get_node(node_id)
            if node is None:
                raise ValueError(f"unknown node: {node_id}")
            thread = tx.get_thread(thread_id)
            if thread is None:
                raise ValueError(f"unknown thread: {thread_id}")
            for raw in proposals:
                proposal_id = raw.get("proposal_id")
                if not proposal_id:
                    raise ValueError("proposal missing proposal_id")
                if reserved is not None and proposal_id not in reserved:
                    raise ValueError(
                        f"proposal_id {proposal_id} not in reserved pool"
                    )
                tx.create_proposal(
                    Proposal(
                        proposal_id=proposal_id,
                        node_id=node_id,
                        thread_id=thread_id,
                        instruction=raw["instruction"],
                        rationale=raw.get("rationale", {}),
                        status="queued",
                        created_at=now,
                    )
                )
                created.append(tx.get_proposal(proposal_id))
            tx.update_thread_last_active(thread_id, now)
            if final_snapshot_ref:
                tx.update_thread_snapshot_ref(thread_id, final_snapshot_ref)
        return created

    def allocate_proposer(
        self,
        *,
        node_id: str,
        thread_id: str,
        proposal_slots: int = 1,
    ) -> ProposerAllocation:
        """Record that a proposer slot was allocated to a node/thread.

        Pre-reserves ``proposal_slots`` proposal ids (§2.4 identity-first):
        the ids are issued here by the single writer, handed to the worker,
        and become both the snapshot filename and the L2 proposal_id.
        """
        now = time.time()
        allocation_id = _new_id()
        reserved = tuple(_new_id() for _ in range(max(1, proposal_slots)))
        allocation = ProposerAllocation(
            allocation_id=allocation_id,
            node_id=node_id,
            thread_id=thread_id,
            reserved_proposal_ids=reserved,
            started_at=now,
            finished_at=None,
            proposals_produced=0,
        )
        with self.transaction() as tx:
            tx.create_allocation(allocation)
        return allocation

    def deallocate_proposer(
        self,
        *,
        allocation_id: str,
        proposals_produced: int = 0,
    ) -> None:
        """Close a proposer allocation."""
        now = time.time()
        with self.transaction() as tx:
            tx.finish_allocation(allocation_id, proposals_produced, now)

    def get_allocation(self, allocation_id: str) -> ProposerAllocation | None:
        with self.transaction() as tx:
            return tx.get_allocation(allocation_id)

    def open_allocations(self) -> list[ProposerAllocation]:
        """Return proposer allocations that are still in flight."""
        with self.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT * FROM proposer_allocations WHERE finished_at IS NULL"
            ).fetchall()
            return [_proposer_allocation_from_row(row) for row in rows]

    def attempts_for_work(
        self,
        logical_work_id: str,
        kind: str,
    ) -> list[Attempt]:
        """Return attempts (oldest first) for a logical work id."""
        with self.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT * FROM attempts WHERE logical_work_id = ? AND kind = ? "
                "ORDER BY created_at",
                (logical_work_id, kind),
            ).fetchall()
            return [_attempt_from_row(row) for row in rows]

    def mark_experiment_infra_failed(
        self,
        *,
        experiment_id: str,
        attempt_id: str,
    ) -> None:
        """An infra failure reopens the experiment for a fresh attempt.

        Scientific status is untouched (still pending/running); the failed
        Attempt is recorded and the experiment returns to ``pending`` so the
        Scheduler can allocate a new Attempt (§16/§17).
        """
        with self.transaction() as tx:
            tx.update_attempt_status(attempt_id=attempt_id, status="failed", finished_at=time.time())
            tx._conn.execute(
                "UPDATE experiments SET status = 'pending' WHERE experiment_id = ?",
                (experiment_id,),
            )

    def mark_proposer_infra_failed(
        self,
        *,
        allocation_id: str,
        attempt_id: str,
    ) -> None:
        """An infra failure on a proposer job keeps the allocation open for retry."""
        with self.transaction() as tx:
            tx.update_attempt_status(attempt_id=attempt_id, status="failed", finished_at=time.time())

    def mark_attempt_succeeded(self, attempt_id: str) -> None:
        with self.transaction() as tx:
            tx.update_attempt_status(attempt_id=attempt_id, status="succeeded", finished_at=time.time())

    def mark_attempt_lost(self, attempt_id: str) -> None:
        with self.transaction() as tx:
            tx.update_attempt_status(attempt_id=attempt_id, status="lost", finished_at=time.time())

    def open_experiments(self) -> list[Experiment]:
        """Return experiments that still need a scientific terminal result."""
        with self.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT * FROM experiments WHERE status IN ('pending','running')"
            ).fetchall()
            return [_experiment_from_row(row) for row in rows]

    def mark_experiment_running(self, experiment_id: str) -> None:
        with self.transaction() as tx:
            tx._conn.execute(
                "UPDATE experiments SET status = 'running' WHERE experiment_id = ?",
                (experiment_id,),
            )

    def count_running_attempts(self, kind: str) -> int:
        """Count attempts currently marked running for a work kind."""
        with self.transaction() as tx:
            row = tx._conn.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE kind = ? AND status = 'running'",
                (kind,),
            ).fetchone()
            return int(row["n"])

    def running_attempts(self, kind: str) -> list[Attempt]:
        """Return attempts currently marked running for a work kind."""
        with self.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT * FROM attempts WHERE kind = ? AND status = 'running'",
                (kind,),
            ).fetchall()
            return [_attempt_from_row(row) for row in rows]

    def mark_running_attempts_lost(self) -> int:
        """Mark every running attempt lost (local-subprocess semantics).

        A local subprocess does not survive its parent scheduler, so on startup
        any attempt that was ``running`` is presumed dead and becomes
        re-submittable.  An HTCondor adapter replaces this by querying the
        external scheduler (§18).
        """
        now = time.time()
        with self.transaction() as tx:
            cur = tx._conn.execute(
                "UPDATE attempts SET status = 'lost', finished_at = ? "
                "WHERE status = 'running'",
                (now,),
            )
            return cur.rowcount

    def record_attempt(
        self,
        *,
        logical_work_id: str,
        kind: str,
        status: str = "ready",
        trace_ref: str | None = None,
        artifact_ref: str | None = None,
        host: str | None = None,
        started_at: float | None = None,
    ) -> Attempt:
        """Record a new execution attempt for a logical work unit."""
        with self.transaction() as tx:
            return tx.create_attempt(
                logical_work_id=logical_work_id,
                kind=kind,
                status=status,
                trace_ref=trace_ref,
                artifact_ref=artifact_ref,
                host=host,
                started_at=started_at,
            )

    def update_attempt_status(
        self,
        *,
        attempt_id: str,
        status: str,
        trace_ref: str | None = None,
        artifact_ref: str | None = None,
        host: str | None = None,
        finished_at: float | None = None,
    ) -> None:
        """Update the status of an existing attempt."""
        with self.transaction() as tx:
            tx.update_attempt_status(
                attempt_id=attempt_id,
                status=status,
                trace_ref=trace_ref,
                artifact_ref=artifact_ref,
                host=host,
                finished_at=finished_at,
            )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self.transaction() as tx:
            return tx.get_proposal(proposal_id)

    def transition_proposal_status(
        self,
        proposal_id: str,
        status: str,
    ) -> None:
        with self.transaction() as tx:
            tx.transition_proposal_status(proposal_id, status)

    def queued_proposals(
        self,
        node_ids: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> list[Proposal]:
        """Return queued proposals in FIFO order."""
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
        with self.transaction() as tx:
            rows = tx._conn.execute(sql, params).fetchall()
            return [_proposal_from_row(row) for row in rows]

    def _with_conn(self, fn):
        """Execute a function with a fresh connection (read-only safe)."""
        conn = self._connect()
        try:
            return fn(conn)
        finally:
            conn.close()


class _Transaction:
    """Internal: one SQLite transaction boundary."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create_node(
        self,
        *,
        node_id: str | None = None,
        parent_node_id: str | None,
        experiment_id: str | None,
        sha: str,
        metrics: dict[str, Any],
        gate_result: GateDecision,
        depth: int,
        status: str,
        created_at: float | None = None,
    ) -> Node:
        now = created_at or time.time()
        nid = node_id or _new_id()
        self._conn.execute(
            """
            INSERT INTO nodes
            (node_id, parent_node_id, experiment_id, sha, metrics,
             gate_result, depth, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nid,
                parent_node_id,
                experiment_id,
                sha,
                _json(metrics),
                _json(_gate_to_dict(gate_result)),
                depth,
                status,
                now,
            ),
        )
        return self.get_node(nid)

    def get_node(self, node_id: str) -> Node | None:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        return None if row is None else _node_from_row(row)

    def get_node_by_sha(self, sha: str) -> Node | None:
        row = self._conn.execute(
            "SELECT * FROM nodes WHERE sha = ?", (sha,)
        ).fetchone()
        return None if row is None else _node_from_row(row)

    def list_active_nodes(self) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE status = 'active' ORDER BY created_at"
        ).fetchall()
        return [_node_from_row(row) for row in rows]

    def load_frontier_axes(self) -> list[FrontierAxis]:
        rows = self._conn.execute("SELECT * FROM frontier_axes").fetchall()
        return [
            FrontierAxis(
                axis=row["axis"],
                node_id=row["node_id"],
                value=row["value"],
                margin=row["margin"],
                hysteresis_anchor=row["hysteresis_anchor"],
                since=row["since"],
            )
            for row in rows
        ]

    def update_frontier_axes(self, axes: list[FrontierAxis]) -> None:
        self._conn.execute("DELETE FROM frontier_axes")
        for ax in axes:
            self._conn.execute(
                """
                INSERT INTO frontier_axes
                (axis, node_id, value, margin, hysteresis_anchor, since)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ax.axis,
                    ax.node_id,
                    ax.value,
                    ax.margin,
                    ax.hysteresis_anchor,
                    ax.since,
                ),
            )

    def create_thread(
        self,
        *,
        thread_id: str | None = None,
        parent_thread_id: str | None,
        node_id: str,
        snapshot_ref: str,
        created_at: float | None = None,
    ) -> Thread:
        now = created_at or time.time()
        tid = thread_id or _new_id()
        self._conn.execute(
            """
            INSERT INTO threads
            (thread_id, parent_thread_id, node_id, snapshot_ref,
             created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (tid, parent_thread_id, node_id, snapshot_ref, now, now),
        )
        return self.get_thread(tid)

    def get_thread(self, thread_id: str) -> Thread | None:
        row = self._conn.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return None if row is None else _thread_from_row(row)

    def update_thread_last_active(self, thread_id: str, when: float) -> None:
        self._conn.execute(
            "UPDATE threads SET last_active_at = ? WHERE thread_id = ?",
            (when, thread_id),
        )

    def update_thread_snapshot_ref(self, thread_id: str, snapshot_ref: str) -> None:
        self._conn.execute(
            "UPDATE threads SET snapshot_ref = ? WHERE thread_id = ?",
            (snapshot_ref, thread_id),
        )

    def create_experiment(
        self,
        *,
        experiment_id: str | None = None,
        proposal_id: str,
        parent_node_id: str,
        status: str = "pending",
        created_at: float | None = None,
    ) -> Experiment:
        now = created_at or time.time()
        eid = experiment_id or _new_id()
        self._conn.execute(
            """
            INSERT INTO experiments
            (experiment_id, proposal_id, parent_node_id, result_sha,
             metrics, gate_result, status, child_node_id, created_at)
            VALUES (?, ?, ?, NULL, '{}', '{}', ?, NULL, ?)
            """,
            (eid, proposal_id, parent_node_id, status, now),
        )
        return self.get_experiment(eid)

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return None if row is None else _experiment_from_row(row)

    def update_experiment_result(
        self,
        *,
        experiment_id: str,
        result_sha: str | None,
        metrics: dict[str, Any],
        gate_result: GateDecision,
        status: str,
        changed_paths: Iterable[str] = (),
    ) -> None:
        self._conn.execute(
            """
            UPDATE experiments
            SET result_sha = ?,
                metrics = ?,
                gate_result = ?,
                status = ?,
                changed_paths = ?
            WHERE experiment_id = ?
            """,
            (
                result_sha,
                _json(metrics),
                _json(_gate_to_dict(gate_result)),
                status,
                _json(list(changed_paths)),
                experiment_id,
            ),
        )

    def link_experiment_child(
        self,
        experiment_id: str,
        child_node_id: str,
    ) -> None:
        self._conn.execute(
            "UPDATE experiments SET child_node_id = ? WHERE experiment_id = ?",
            (child_node_id, experiment_id),
        )

    def transition_proposal_status(
        self,
        proposal_id: str,
        status: str,
    ) -> None:
        self._conn.execute(
            "UPDATE proposals SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )

    def create_proposal(self, proposal: Proposal) -> Proposal:
        self._conn.execute(
            """
            INSERT INTO proposals
            (proposal_id, node_id, thread_id, instruction, rationale,
             status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.proposal_id,
                proposal.node_id,
                proposal.thread_id,
                proposal.instruction,
                _json(proposal.rationale),
                proposal.status,
                proposal.created_at,
            ),
        )
        return proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        row = self._conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return None if row is None else _proposal_from_row(row)

    def create_attempt(
        self,
        *,
        attempt_id: str | None = None,
        logical_work_id: str,
        kind: str,
        status: str = "ready",
        trace_ref: str | None = None,
        artifact_ref: str | None = None,
        host: str | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        created_at: float | None = None,
    ) -> Attempt:
        now = created_at or time.time()
        aid = attempt_id or _new_id()
        self._conn.execute(
            """
            INSERT INTO attempts
            (attempt_id, logical_work_id, kind, status, trace_ref,
             artifact_ref, host, started_at, finished_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aid, logical_work_id, kind, status, trace_ref,
                artifact_ref, host, started_at, finished_at, now,
            ),
        )
        return self.get_attempt(aid)

    def get_attempt(self, attempt_id: str) -> Attempt | None:
        row = self._conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        return None if row is None else _attempt_from_row(row)

    def update_attempt_status(
        self,
        *,
        attempt_id: str,
        status: str,
        trace_ref: str | None = None,
        artifact_ref: str | None = None,
        host: str | None = None,
        finished_at: float | None = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE attempts
            SET status = ?,
                trace_ref = COALESCE(?, trace_ref),
                artifact_ref = COALESCE(?, artifact_ref),
                host = COALESCE(?, host),
                finished_at = COALESCE(?, finished_at)
            WHERE attempt_id = ?
            """,
            (
                status,
                trace_ref,
                artifact_ref,
                host,
                finished_at,
                attempt_id,
            ),
        )

    def create_allocation(self, allocation: ProposerAllocation) -> None:
        self._conn.execute(
            """
            INSERT INTO proposer_allocations
            (allocation_id, node_id, thread_id, reserved_proposal_ids,
             started_at, finished_at, proposals_produced)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                allocation.allocation_id,
                allocation.node_id,
                allocation.thread_id,
                _json(list(allocation.reserved_proposal_ids)),
                allocation.started_at,
                allocation.finished_at,
                allocation.proposals_produced,
            ),
        )

    def finish_allocation(
        self,
        allocation_id: str,
        proposals_produced: int,
        when: float,
    ) -> None:
        self._conn.execute(
            """
            UPDATE proposer_allocations
            SET finished_at = ?, proposals_produced = ?
            WHERE allocation_id = ?
            """,
            (when, proposals_produced, allocation_id),
        )

    def get_allocation(self, allocation_id: str) -> ProposerAllocation | None:
        row = self._conn.execute(
            "SELECT * FROM proposer_allocations WHERE allocation_id = ?",
            (allocation_id,),
        ).fetchone()
        return None if row is None else _proposer_allocation_from_row(row)


# ---------------------------------------------------------------------------
# Row deserialisation
# ---------------------------------------------------------------------------

def _gate_to_dict(gate: GateDecision) -> dict[str, Any]:
    return {
        "passed": gate.passed,
        "results": {
            name: {"passed": gr.passed, "detail": gr.detail}
            for name, gr in gate.results.items()
        },
    }


def _gate_from_dict(raw: dict[str, Any]) -> GateDecision:
    return GateDecision(
        results={
            name: GateResult(gr.get("passed"), gr.get("detail", ""))
            for name, gr in raw.get("results", {}).items()
        },
        passed=raw.get("passed", False),
    )


def _node_from_row(row: sqlite3.Row) -> Node:
    return Node(
        node_id=row["node_id"],
        parent_node_id=row["parent_node_id"],
        experiment_id=row["experiment_id"],
        sha=row["sha"],
        metrics=_unjson(row["metrics"]),
        gate_result=_gate_from_dict(_unjson(row["gate_result"])),
        depth=row["depth"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        thread_id=row["thread_id"],
        parent_thread_id=row["parent_thread_id"],
        node_id=row["node_id"],
        snapshot_ref=row["snapshot_ref"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
    )


def _proposal_from_row(row: sqlite3.Row) -> Proposal:
    return Proposal(
        proposal_id=row["proposal_id"],
        node_id=row["node_id"],
        thread_id=row["thread_id"],
        instruction=row["instruction"],
        rationale=_unjson(row["rationale"]),
        status=row["status"],
        created_at=row["created_at"],
    )


def _experiment_from_row(row: sqlite3.Row) -> Experiment:
    return Experiment(
        experiment_id=row["experiment_id"],
        proposal_id=row["proposal_id"],
        parent_node_id=row["parent_node_id"],
        result_sha=row["result_sha"],
        metrics=_unjson(row["metrics"]),
        gate_result=_gate_from_dict(_unjson(row["gate_result"])),
        status=row["status"],
        changed_paths=tuple(_unjson(row["changed_paths"])),
        child_node_id=row["child_node_id"],
        created_at=row["created_at"],
    )


def _proposer_allocation_from_row(row: sqlite3.Row) -> ProposerAllocation:
    return ProposerAllocation(
        allocation_id=row["allocation_id"],
        node_id=row["node_id"],
        thread_id=row["thread_id"],
        reserved_proposal_ids=tuple(_unjson(row["reserved_proposal_ids"])),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        proposals_produced=row["proposals_produced"],
    )


def _attempt_from_row(row: sqlite3.Row) -> Attempt:
    return Attempt(
        attempt_id=row["attempt_id"],
        logical_work_id=row["logical_work_id"],
        kind=row["kind"],
        status=row["status"],
        trace_ref=row["trace_ref"],
        artifact_ref=row["artifact_ref"],
        host=row["host"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        created_at=row["created_at"],
    )
