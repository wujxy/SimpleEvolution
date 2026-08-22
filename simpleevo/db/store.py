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

from simpleevo.research_state import CognitiveTransformation, ResearchState

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
class Episode:
    episode_id: str
    inherited_from_episode_id: str | None
    node_id: str
    variation_operator: str | None
    created_at: float
    last_active_at: float


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    node_id: str
    episode_id: str
    instruction: str
    rationale: dict[str, Any]
    status: str
    created_at: float
    research_state_id: str | None = None
    research_operation: str | None = None
    donor_experiment_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Epoch:
    epoch_id: str
    root_node_id: str
    previous_epoch_id: str | None
    created_at: float


@dataclass(frozen=True)
class IntegrationRequest:
    integration_request_id: str
    epoch_id: str
    target_node_id: str
    donor_experiment_ids: tuple[str, ...]
    selection_rationale: str
    status: str
    created_at: float
    integrator_episode_id: str | None = None
    proposal_id: str | None = None
    experiment_id: str | None = None
    closed_at: float | None = None


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
    episode_id: str
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
            self._migrate_attempt_kinds(conn)
            conn.commit()

            root = conn.execute(
                "SELECT node_id FROM nodes WHERE parent_node_id IS NULL "
                "ORDER BY created_at, node_id LIMIT 1"
            ).fetchone()
            has_epoch = conn.execute("SELECT 1 FROM epochs LIMIT 1").fetchone()
            if root is not None and has_epoch is None:
                conn.execute(
                    "INSERT INTO epochs VALUES (?, ?, NULL, ?)",
                    ("epoch-0", root["node_id"], time.time()),
                )

    @staticmethod
    def _migrate_attempt_kinds(conn: sqlite3.Connection) -> None:
        """Expand the old two-worker CHECK without discarding attempt history."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='attempts'"
        ).fetchone()
        if row is None or "'supervisor'" in (row["sql"] or ""):
            return
        conn.execute("""
            CREATE TABLE attempts_v2 (
                attempt_id TEXT PRIMARY KEY,
                logical_work_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN (
                    'proposer', 'experiment', 'supervisor', 'integrator'
                )),
                status TEXT NOT NULL DEFAULT 'ready' CHECK (status IN (
                    'ready', 'pending', 'running', 'succeeded', 'failed', 'lost'
                )),
                trace_ref TEXT, artifact_ref TEXT, host TEXT,
                started_at REAL, finished_at REAL, created_at REAL NOT NULL
            )
        """)
        conn.execute("INSERT INTO attempts_v2 SELECT * FROM attempts")
        conn.execute("DROP TABLE attempts")
        conn.execute("ALTER TABLE attempts_v2 RENAME TO attempts")
        conn.execute(
            "CREATE INDEX idx_attempts_work ON attempts(logical_work_id, kind)"
        )
        conn.execute("CREATE INDEX idx_attempts_status ON attempts(status)")
    @contextmanager
    def transaction(self, *, immediate: bool = False):
        """Context manager yielding a Transaction object."""
        conn = self._connect()
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
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

                # Preserve Episode lineage. Runtime cognition for a Child is
                # seeded proposal-specifically from ResearchState + outcome;
                # inherited_from remains provenance, not session-copy policy.
                proposal = tx.get_proposal(exp.proposal_id)
                if proposal is not None:
                    tx.create_episode(
                        inherited_from_episode_id=proposal.episode_id,
                        node_id=child.node_id,
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
                                margin=0.0,
                                hysteresis_anchor=None,
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
        episode_id: str,
        proposals: Iterable[dict[str, Any]],
        reserved_proposal_ids: Iterable[str] | None = None,
    ) -> list[Proposal]:
        """Atomically publish a batch of fully-formed proposals.

        Each proposal must carry its own ``proposal_id`` (issued by the
        Scheduler when the proposer allocation was created, §2.4
        identity-first).  When ``reserved_proposal_ids`` is given, every
        incoming id is validated against it so a worker cannot mint ids.
        """
        reserved = set(reserved_proposal_ids) if reserved_proposal_ids is not None else None
        created: list[Proposal] = []
        now = time.time()
        with self.transaction() as tx:
            node = tx.get_node(node_id)
            if node is None:
                raise ValueError(f"unknown node: {node_id}")
            episode = tx.get_episode(episode_id)
            if episode is None:
                raise ValueError(f"unknown episode: {episode_id}")
            for raw in proposals:
                proposal_id = raw.get("proposal_id")
                if not proposal_id:
                    raise ValueError("proposal missing proposal_id")
                if reserved is not None and proposal_id not in reserved:
                    raise ValueError(
                        f"proposal_id {proposal_id} not in reserved pool"
                    )
                operation = raw.get("research_operation")
                donors = tuple(raw.get("donor_experiment_ids", ()))
                if operation == "explore" and donors:
                    raise ValueError("explore proposals cannot name donors")
                if operation == "synthesize" and not donors:
                    raise ValueError("synthesize proposals require donors")
                if operation not in {None, "explore", "synthesize"}:
                    raise ValueError("unknown research operation")
                tx.create_proposal(
                    Proposal(
                        proposal_id=proposal_id,
                        node_id=node_id,
                        episode_id=episode_id,
                        instruction=raw["instruction"],
                        rationale=raw.get("rationale", {}),
                        status="queued",
                        created_at=now,
                        research_operation=operation,
                        donor_experiment_ids=donors,
                    )
                )
                created.append(tx.get_proposal(proposal_id))
            tx.update_episode_last_active(episode_id, now)
        return created

    def publish_research_batch(
        self,
        *,
        node_id: str,
        episode_id: str,
        transformations: Iterable[dict[str, Any]],
        research_states: Iterable[dict[str, Any]],
        proposals: Iterable[dict[str, Any]],
        reserved_proposal_ids: Iterable[str] | None = None,
    ) -> list[Proposal]:
        """Atomically publish one Episode's cognitive records and proposals."""
        transformation_rows = list(transformations)
        state_rows = list(research_states)
        proposal_rows = list(proposals)
        reserved = (
            set(reserved_proposal_ids)
            if reserved_proposal_ids is not None else None
        )
        now = time.time()
        created: list[Proposal] = []

        with self.transaction() as tx:
            node = tx.get_node(node_id)
            if node is None:
                raise ValueError(f"unknown node: {node_id}")
            episode = tx.get_episode(episode_id)
            if episode is None:
                raise ValueError(f"unknown episode: {episode_id}")
            if episode.node_id != node_id:
                raise ValueError("episode belongs to another node")

            transformation_ids = [
                row.get("transformation_id") for row in transformation_rows
            ]
            state_ids = [row.get("research_state_id") for row in state_rows]
            proposal_ids = [row.get("proposal_id") for row in proposal_rows]
            self._validate_unique_ids(
                transformation_ids, "transformation_id",
            )
            self._validate_unique_ids(state_ids, "research_state_id")
            self._validate_unique_ids(proposal_ids, "proposal_id")

            incoming_transformations = set(transformation_ids)
            incoming_states = set(state_ids)
            for transformation_id in incoming_transformations:
                if not transformation_id.startswith(f"ct-{episode_id}-"):
                    raise ValueError(
                        f"invalid transformation_id for episode: {transformation_id}"
                    )
                if tx.get_cognitive_transformation(transformation_id) is not None:
                    raise ValueError(
                        f"duplicate transformation_id: {transformation_id}"
                    )
            for state_id in incoming_states:
                if not state_id.startswith(f"rs-{episode_id}-"):
                    raise ValueError(
                        f"invalid research_state_id for episode: {state_id}"
                    )
                if tx.get_research_state(state_id) is not None:
                    raise ValueError(f"duplicate research_state_id: {state_id}")

            for raw in transformation_rows:
                if raw.get("node_id") != node_id or raw.get("episode_id") != episode_id:
                    raise ValueError(
                        "transformation belongs to another node or episode"
                    )
                source_id = raw.get("source_research_state_id")
                if (
                    source_id
                    and source_id not in incoming_states
                    and tx.get_research_state(source_id) is None
                ):
                    raise ValueError(f"unknown source_research_state_id: {source_id}")

            for raw in state_rows:
                if raw.get("node_id") != node_id or raw.get("episode_id") != episode_id:
                    raise ValueError(
                        "research state belongs to another node or episode"
                    )
                derived_id = raw.get("derived_from_research_state_id")
                if (
                    derived_id
                    and derived_id not in incoming_states
                    and tx.get_research_state(derived_id) is None
                ):
                    raise ValueError(
                        f"unknown derived_from_research_state_id: {derived_id}"
                    )
                transformation_id = raw.get("transformation_id")
                if (
                    transformation_id
                    and transformation_id not in incoming_transformations
                    and tx.get_cognitive_transformation(transformation_id) is None
                ):
                    raise ValueError(
                        f"unknown transformation_id: {transformation_id}"
                    )

            for raw in transformation_rows:
                tx.create_cognitive_transformation(CognitiveTransformation(
                    transformation_id=raw["transformation_id"],
                    node_id=node_id,
                    episode_id=episode_id,
                    source_research_state_id=raw.get("source_research_state_id"),
                    operator_id=raw["operator_id"],
                    challenge=raw["challenge"],
                    created_at=float(raw.get("created_at", now)),
                ))
            for raw in state_rows:
                tx.create_research_state(ResearchState(
                    research_state_id=raw["research_state_id"],
                    node_id=node_id,
                    episode_id=episode_id,
                    derived_from_research_state_id=raw.get(
                        "derived_from_research_state_id"
                    ),
                    transformation_id=raw.get("transformation_id"),
                    working_model=raw["working_model"],
                    evidence_refs=tuple(raw.get("evidence_refs", ())),
                    created_at=float(raw.get("created_at", now)),
                ))

            for raw in proposal_rows:
                proposal_id = raw.get("proposal_id")
                if reserved is not None and proposal_id not in reserved:
                    raise ValueError(
                        f"proposal_id {proposal_id} not in reserved pool"
                    )
                state_id = raw.get("research_state_id")
                state = tx.get_research_state(state_id) if state_id else None
                if state is None:
                    raise ValueError(f"unknown research_state_id: {state_id}")
                if state.node_id != node_id or state.episode_id != episode_id:
                    raise ValueError(
                        "proposal research state belongs to another node or episode"
                    )
                operation = raw.get("research_operation")
                donors = tuple(raw.get("donor_experiment_ids", ()))
                if operation == "explore" and donors:
                    raise ValueError("explore proposals cannot name donors")
                if operation == "synthesize" and not donors:
                    raise ValueError("synthesize proposals require donors")
                if operation not in {None, "explore", "synthesize"}:
                    raise ValueError("unknown research operation")
                proposal = tx.create_proposal(Proposal(
                    proposal_id=proposal_id,
                    node_id=node_id,
                    episode_id=episode_id,
                    instruction=raw["instruction"],
                    rationale=raw.get("rationale", {}),
                    status="queued",
                    created_at=now,
                    research_state_id=state_id,
                    research_operation=operation,
                    donor_experiment_ids=donors,
                ))
                created.append(proposal)
            tx.update_episode_last_active(episode_id, now)
        return created

    @staticmethod
    def _validate_unique_ids(values: list[Any], field: str) -> None:
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"missing {field}")
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {field}")

    def allocate_proposer(
        self,
        *,
        node_id: str,
        episode_id: str,
        proposal_slots: int = 1,
        max_proposals_per_node: int = 9,
    ) -> ProposerAllocation | None:
        """Record that a proposer slot was allocated to a node/episode.

        Pre-reserves ``proposal_slots`` proposal ids (§2.4 identity-first):
        the ids are issued here by the single writer and handed to the worker.
        """
        with self.transaction(immediate=True) as tx:
            node = tx.get_node(node_id)
            episode = tx.get_episode(episode_id)
            if node is None:
                raise ValueError(f"unknown node: {node_id}")
            if episode is None or episode.node_id != node_id:
                raise ValueError("episode belongs to another node")
            published = tx._conn.execute(
                "SELECT COUNT(*) FROM proposals WHERE node_id = ?",
                (node_id,),
            ).fetchone()[0]
            open_rows = tx._conn.execute(
                "SELECT reserved_proposal_ids FROM proposer_allocations "
                "WHERE node_id = ? AND finished_at IS NULL",
                (node_id,),
            ).fetchall()
            open_reserved = sum(
                len(_unjson(row["reserved_proposal_ids"])) for row in open_rows
            )
            remaining = max(
                0, max_proposals_per_node - published - open_reserved,
            )
            reserved_count = min(max(0, proposal_slots), remaining)
            if reserved_count == 0:
                return None
            now = time.time()
            allocation = ProposerAllocation(
                allocation_id=_new_id(),
                node_id=node_id,
                episode_id=episode_id,
                reserved_proposal_ids=tuple(
                    _new_id() for _ in range(reserved_count)
                ),
                started_at=now,
                finished_at=None,
                proposals_produced=0,
            )
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

    def allocated_episode_ids(self) -> set[str]:
        """Return every episode id that has ever been allocated (single-use).

        A Scientist Episode is ONE research act (§3.4): once it has any
        allocation row — open (in-flight) or closed (terminal) — it must never
        be scheduled again.  Only episodes with no allocation row are fresh
        and eligible for a first run.
        """
        with self.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT DISTINCT episode_id FROM proposer_allocations"
            ).fetchall()
            return {row["episode_id"] for row in rows}

    def count_allocations_for_node(self, node_id: str) -> int:
        """Total proposer allocations a node has received over its lifetime.

        Used as the reseed budget: a frontier node whose fresh episodes are
        exhausted may be programmatically re-studied until this count reaches
        ``max_research_per_node``.
        """
        with self.transaction() as tx:
            row = tx._conn.execute(
                "SELECT COUNT(*) AS n FROM proposer_allocations WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            return int(row["n"])

    def set_node_metrics(self, node_id: str, metrics: dict[str, Any]) -> None:
        """Record measured metrics on an existing node (run-start baseline).

        The root node is seeded with empty metrics at ``init``; the run-start
        baseline evaluation writes the pristine source's objective here so the
        reporting projections have a real relative anchor.
        """
        with self.transaction() as tx:
            tx._conn.execute(
                "UPDATE nodes SET metrics = ? WHERE node_id = ?",
                (_json(metrics), node_id),
            )

    def episode_allocation_finished(self, episode_id: str) -> bool:
        """Whether an episode's most recent proposer allocation has finished.

        A reseed must not fork an in-flight scientist: until its allocation is
        closed (``finished_at`` set), the episode has no frozen final cognition
        for a child episode to inherit.
        """
        with self.transaction() as tx:
            row = tx._conn.execute(
                "SELECT finished_at FROM proposer_allocations "
                "WHERE episode_id = ? ORDER BY started_at DESC LIMIT 1",
                (episode_id,),
            ).fetchone()
            return row is not None and row["finished_at"] is not None

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

    def mark_attempt_failed(self, attempt_id: str) -> None:
        with self.transaction() as tx:
            tx.update_attempt_status(attempt_id=attempt_id, status="failed", finished_at=time.time())

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

    def current_epoch(self) -> Epoch | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM epochs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _epoch_from_row(row)

    def record_scheduler_event(self, event_type: str, payload: dict[str, Any]) -> str:
        event_id = _new_id()
        with self.transaction() as tx:
            tx._conn.execute(
                "INSERT INTO scheduler_events (event_id, type, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, event_type, _json(payload), time.time()),
            )
        return event_id

    def latest_scheduler_event(self, event_type: str) -> dict[str, Any] | None:
        with self.transaction() as tx:
            row = tx._conn.execute(
                "SELECT payload FROM scheduler_events WHERE type = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (event_type,),
            ).fetchone()
        return None if row is None else json.loads(row["payload"])

    def create_integration_request(
        self,
        *,
        integration_request_id: str,
        epoch_id: str,
        target_node_id: str,
        donor_experiment_ids: tuple[str, ...],
        selection_rationale: str,
    ) -> IntegrationRequest:
        with self.transaction() as tx:
            existing = tx.get_integration_request(integration_request_id)
            if existing is not None:
                if existing != IntegrationRequest(
                    integration_request_id=integration_request_id,
                    epoch_id=epoch_id,
                    target_node_id=target_node_id,
                    donor_experiment_ids=donor_experiment_ids,
                    selection_rationale=selection_rationale,
                    status="open",
                    created_at=existing.created_at,
                    integrator_episode_id=existing.integrator_episode_id,
                    proposal_id=existing.proposal_id,
                    experiment_id=existing.experiment_id,
                    closed_at=existing.closed_at,
                ):
                    raise ValueError("integration request identity conflict")
                return existing
            return tx.create_integration_request(
                integration_request_id=integration_request_id,
                epoch_id=epoch_id,
                target_node_id=target_node_id,
                donor_experiment_ids=donor_experiment_ids,
                selection_rationale=selection_rationale,
            )

    def get_integration_request(self, integration_request_id: str) -> IntegrationRequest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM integration_requests WHERE integration_request_id = ?",
                (integration_request_id,),
            ).fetchone()
        return None if row is None else _integration_request_from_row(row)

    def integration_requests(self, *statuses: str) -> list[IntegrationRequest]:
        sql = "SELECT * FROM integration_requests"
        params: tuple[str, ...] = ()
        if statuses:
            sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            params = tuple(statuses)
        sql += " ORDER BY created_at"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_integration_request_from_row(row) for row in rows]

    def prepare_integration_request(self, request_id: str) -> IntegrationRequest:
        with self.transaction() as tx:
            request = tx.get_integration_request(request_id)
            if request is None or request.status != "open":
                raise ValueError("integration request is not open")
            if request.integrator_episode_id is None:
                episode = tx.create_episode(node_id=request.target_node_id)
                tx._conn.execute(
                    "UPDATE integration_requests SET integrator_episode_id = ? "
                    "WHERE integration_request_id = ?",
                    (episode.episode_id, request_id),
                )
            return tx.get_integration_request(request_id)

    def finish_integration_request(
        self,
        request_id: str,
        *,
        status: str,
        proposal_id: str | None = None,
        experiment_id: str | None = None,
    ) -> IntegrationRequest:
        if status not in {"abstained", "submitted", "closed", "promoted"}:
            raise ValueError("invalid integration request status")
        closed_at = time.time() if status in {"abstained", "closed", "promoted"} else None
        with self.transaction() as tx:
            tx._conn.execute(
                "UPDATE integration_requests SET status = ?, "
                "proposal_id = COALESCE(?, proposal_id), "
                "experiment_id = COALESCE(?, experiment_id), closed_at = ? "
                "WHERE integration_request_id = ?",
                (status, proposal_id, experiment_id, closed_at, request_id),
            )
            request = tx.get_integration_request(request_id)
            if request is None:
                raise ValueError("unknown integration request")
            return request

    def link_integration_experiment(
        self, proposal_id: str, experiment_id: str,
    ) -> None:
        with self.transaction() as tx:
            updated = tx._conn.execute(
                "UPDATE integration_requests SET experiment_id = ? "
                "WHERE proposal_id = ? AND status = 'submitted'",
                (experiment_id, proposal_id),
            )
            if updated.rowcount > 1:
                raise ValueError("proposal belongs to multiple integration requests")



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
        if parent_node_id is None:
            self._conn.execute(
                "INSERT OR IGNORE INTO epochs VALUES (?, ?, NULL, ?)",
                ("epoch-0", nid, now),
            )

        return self.get_node(nid)

    def create_integration_request(
        self,
        *,
        integration_request_id: str,
        epoch_id: str,
        target_node_id: str,
        donor_experiment_ids: tuple[str, ...],
        selection_rationale: str,
    ) -> IntegrationRequest:
        if not donor_experiment_ids:
            raise ValueError("integration request requires donors")
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO integration_requests
            (integration_request_id, epoch_id, target_node_id,
             donor_experiment_ids, selection_rationale, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (integration_request_id, epoch_id, target_node_id,
             _json(list(donor_experiment_ids)), selection_rationale, now),
        )
        return self.get_integration_request(integration_request_id)

    def get_integration_request(self, integration_request_id: str) -> IntegrationRequest | None:
        row = self._conn.execute(
            "SELECT * FROM integration_requests WHERE integration_request_id = ?",
            (integration_request_id,),
        ).fetchone()
        return None if row is None else _integration_request_from_row(row)

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

    def create_episode(
        self,
        *,
        episode_id: str | None = None,
        inherited_from_episode_id: str | None = None,
        node_id: str,
        variation_operator: str | None = None,
        created_at: float | None = None,
    ) -> Episode:
        now = created_at or time.time()
        eid = episode_id or _new_id()
        self._conn.execute(
            """
            INSERT INTO episodes
            (episode_id, inherited_from_episode_id, node_id,
             variation_operator, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (eid, inherited_from_episode_id, node_id, variation_operator, now, now),
        )
        return self.get_episode(eid)

    def get_episode(self, episode_id: str) -> Episode | None:
        row = self._conn.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return None if row is None else _episode_from_row(row)

    def update_episode_last_active(self, episode_id: str, when: float) -> None:
        self._conn.execute(
            "UPDATE episodes SET last_active_at = ? WHERE episode_id = ?",
            (when, episode_id),
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
            (proposal_id, node_id, episode_id, instruction, rationale,
             status, created_at, research_state_id, research_operation,
             donor_experiment_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.proposal_id,
                proposal.node_id,
                proposal.episode_id,
                proposal.instruction,
                _json(proposal.rationale),
                proposal.status,
                proposal.created_at,
                getattr(proposal, "research_state_id", None),
                getattr(proposal, "research_operation", None),
                _json(list(getattr(proposal, "donor_experiment_ids", ()))),
            ),
        )
        return proposal

    def create_cognitive_transformation(
        self, transformation: CognitiveTransformation,
    ) -> CognitiveTransformation:
        self._conn.execute(
            """
            INSERT INTO cognitive_transformations
            (transformation_id, node_id, episode_id, source_research_state_id,
             operator_id, challenge, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transformation.transformation_id,
                transformation.node_id,
                transformation.episode_id,
                transformation.source_research_state_id,
                transformation.operator_id,
                transformation.challenge,
                transformation.created_at,
            ),
        )
        return transformation

    def get_cognitive_transformation(
        self, transformation_id: str,
    ) -> CognitiveTransformation | None:
        row = self._conn.execute(
            "SELECT * FROM cognitive_transformations WHERE transformation_id = ?",
            (transformation_id,),
        ).fetchone()
        return None if row is None else _transformation_from_row(row)

    def create_research_state(self, state: ResearchState) -> ResearchState:
        self._conn.execute(
            """
            INSERT INTO research_states
            (research_state_id, node_id, episode_id,
             derived_from_research_state_id, transformation_id, working_model,
             evidence_refs, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.research_state_id,
                state.node_id,
                state.episode_id,
                state.derived_from_research_state_id,
                state.transformation_id,
                state.working_model,
                _json(list(state.evidence_refs)),
                state.created_at,
            ),
        )
        return state

    def get_research_state(self, research_state_id: str) -> ResearchState | None:
        row = self._conn.execute(
            "SELECT * FROM research_states WHERE research_state_id = ?",
            (research_state_id,),
        ).fetchone()
        return None if row is None else _research_state_from_row(row)

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
            (allocation_id, node_id, episode_id, reserved_proposal_ids,
             started_at, finished_at, proposals_produced)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                allocation.allocation_id,
                allocation.node_id,
                allocation.episode_id,
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


def _episode_from_row(row: sqlite3.Row) -> Episode:
    return Episode(
        episode_id=row["episode_id"],
        inherited_from_episode_id=row["inherited_from_episode_id"],
        node_id=row["node_id"],
        variation_operator=row["variation_operator"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
    )


def _proposal_from_row(row: sqlite3.Row) -> Proposal:
    return Proposal(
        proposal_id=row["proposal_id"],
        node_id=row["node_id"],
        episode_id=row["episode_id"],
        instruction=row["instruction"],
        rationale=_unjson(row["rationale"]),
        status=row["status"],
        created_at=row["created_at"],
        research_state_id=row["research_state_id"],
        research_operation=row["research_operation"],
        donor_experiment_ids=tuple(_unjson(row["donor_experiment_ids"])),
    )


def _transformation_from_row(row: sqlite3.Row) -> CognitiveTransformation:
    return CognitiveTransformation(
        transformation_id=row["transformation_id"],
        node_id=row["node_id"],
        episode_id=row["episode_id"],
        source_research_state_id=row["source_research_state_id"],
        operator_id=row["operator_id"],
        challenge=row["challenge"],
        created_at=row["created_at"],
    )


def _research_state_from_row(row: sqlite3.Row) -> ResearchState:
    return ResearchState(
        research_state_id=row["research_state_id"],
        node_id=row["node_id"],
        episode_id=row["episode_id"],
        derived_from_research_state_id=row["derived_from_research_state_id"],
        transformation_id=row["transformation_id"],
        working_model=row["working_model"],
        evidence_refs=tuple(_unjson(row["evidence_refs"])),
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
        episode_id=row["episode_id"],
        reserved_proposal_ids=tuple(_unjson(row["reserved_proposal_ids"])),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        proposals_produced=row["proposals_produced"],
    )


def _epoch_from_row(row: sqlite3.Row) -> Epoch:
    return Epoch(
        epoch_id=row["epoch_id"],
        root_node_id=row["root_node_id"],
        previous_epoch_id=row["previous_epoch_id"],
        created_at=row["created_at"],
    )


def _integration_request_from_row(row: sqlite3.Row) -> IntegrationRequest:
    return IntegrationRequest(
        integration_request_id=row["integration_request_id"],
        epoch_id=row["epoch_id"],
        target_node_id=row["target_node_id"],
        donor_experiment_ids=tuple(_unjson(row["donor_experiment_ids"])),
        selection_rationale=row["selection_rationale"],
        status=row["status"],
        created_at=row["created_at"],
        integrator_episode_id=row["integrator_episode_id"],
        proposal_id=row["proposal_id"],
        experiment_id=row["experiment_id"],
        closed_at=row["closed_at"],
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
