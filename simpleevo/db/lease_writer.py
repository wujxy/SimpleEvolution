"""Worker-side narrow write path for a lease's research state.

The single-writer contract has one deliberate exception (科学家完整研究制
§2.3/§3): the seat itself is the sole author of its understanding, and the
write must survive a mid-lease crash — "调查不蒸发" is a process property,
not a conclusion-time courtesy.  This module is that exception, kept as
narrow as possible:

- one hot row per lease (``research_states`` keyed by ``lease_id``),
  upserted with ``revision+1`` every work cycle;
- one insert-only ledger (``assistant_calls``) for consult/work telemetry;
- ``BEGIN IMMEDIATE`` + a busy timeout so a worker write and the
  scheduler's own transaction serialize instead of failing fast;
- no schema management, no other tables, no deletes.

Operational boundary: this is safe for the local submitter (probe A,
supervisor runs on one machine).  A condor-scale deployment needs a sidecar
JSONL folded in by the scheduler instead — WAL multi-writer over a shared
network filesystem is corruption-prone.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def upsert_lease_research_state(
    db_path: str | Path,
    *,
    lease_id: str,
    episode_id: str,
    node_id: str,
    working_model: str,
    evidence: list[dict[str, Any]] | None = None,
    experiment_log: list[dict[str, Any]] | None = None,
    deliverables: list[dict[str, Any]] | None = None,
    conclusion: dict[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
) -> int:
    """Upsert the lease's single evolving research-state row.

    The row id is stable (``rs-<episode>-head``) so downstream joins and
    the seed/查重 semantics survive; each call bumps ``revision`` and
    replaces the six-block payload wholesale (the full-resolution history
    lives inside ``experiment_log``, which the caller appends to).
    Returns the new revision.
    """
    now = time.time()
    research_state_id = f"rs-{episode_id}-head"
    evidence_json = json.dumps(evidence or [], ensure_ascii=False)
    log_json = json.dumps(experiment_log or [], ensure_ascii=False)
    deliverables_json = json.dumps(deliverables or [], ensure_ascii=False)
    refs_json = json.dumps(evidence_refs or [], ensure_ascii=False)
    conclusion_json = (
        json.dumps(conclusion, ensure_ascii=False)
        if conclusion is not None else None
    )
    conn = _connect(db_path)
    try:
        # BEGIN IMMEDIATE takes the write lock, so update-then-insert is
        # race-free (and avoids relying on which unique constraint an
        # ON CONFLICT clause would fire on — row id and lease_id both
        # point at the same row).
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE research_states SET
                working_model = ?, evidence_refs = ?, evidence = ?,
                experiment_log = ?, deliverables = ?, conclusion = ?,
                revision = COALESCE(revision, 0) + 1
            WHERE lease_id = ?
            """,
            (
                working_model, refs_json, evidence_json,
                log_json, deliverables_json, conclusion_json,
                lease_id,
            ),
        )
        if cur.rowcount == 0:
            conn.execute(
                """
                INSERT INTO research_states
                (research_state_id, node_id, episode_id,
                 derived_from_research_state_id, working_model,
                 evidence_refs, created_at, evidence, experiment_log,
                 deliverables, conclusion, revision, lease_id)
                VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    research_state_id,
                    node_id,
                    episode_id,
                    working_model,
                    refs_json,
                    now,
                    evidence_json,
                    log_json,
                    deliverables_json,
                    conclusion_json,
                    lease_id,
                ),
            )
        row = conn.execute(
            "SELECT revision FROM research_states WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        conn.commit()
        return int(row[0]) if row else 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def append_experiment_log_entry(
    db_path: str | Path,
    *,
    lease_id: str,
    entry: dict[str, Any],
) -> None:
    """Append one work-cycle entry to the lease state's experiment log.

    A convenience wrapper for the mandated per-work-cycle 落账: reads the
    current row, appends the entry, and upserts with revision+1 under one
    immediate transaction.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT working_model, experiment_log, revision FROM "
            "research_states WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no research state row for lease {lease_id}")
        log = json.loads(row["experiment_log"] or "[]")
        log.append(entry)
        conn.execute(
            "UPDATE research_states SET experiment_log = ?, "
            "revision = revision + 1 WHERE lease_id = ?",
            (json.dumps(log, ensure_ascii=False), lease_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_assistant_call(
    db_path: str | Path,
    *,
    call_id: str,
    episode_id: str,
    lease_id: str | None,
    lens: str | None,
    kind: str,
    question_digest: str = "",
    adopted: bool | None = None,
    usage: dict[str, Any] | None = None,
    world_sha: str | None = None,
) -> None:
    """Insert one consult/work ledger row (oracle-homogenization surface)."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO assistant_calls
            (call_id, episode_id, lease_id, lens, kind, question_digest,
             adopted, usage, world_sha, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                episode_id,
                lease_id,
                lens,
                kind,
                question_digest,
                None if adopted is None else int(adopted),
                json.dumps(usage or {}, ensure_ascii=False),
                world_sha,
                time.time(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_call_adopted(
    db_path: str | Path,
    *,
    call_id: str,
    adopted: bool,
) -> None:
    """The seat later reports whether it acted on an assistant answer."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE assistant_calls SET adopted = ? WHERE call_id = ?",
            (int(adopted), call_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
