"""Tests for the L2 schema bootstrap."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from simpleevo.db.schema import ResearchDBSchema


def test_schema_creates_all_tables():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(path))
        ResearchDBSchema.apply(conn)
        conn.commit()

        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cur.fetchall()}
        expected = {
            "nodes",
            "episodes",
            "research_states",
            "cognitive_transformations",
            "proposals",
            "experiments",
            "attempts",
            "frontier_axes",
            "proposer_allocations",
            "scheduler_events",
            "epochs",
            "integration_requests",
            "resource_ledger",
            "assistant_calls",
        }
        assert expected <= tables, f"missing tables: {expected - tables}"
        conn.close()


def test_schema_creates_complete_research_columns():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(path))
        ResearchDBSchema.apply(conn)
        conn.commit()

        state_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(research_states)").fetchall()
        }
        assert {"evidence", "experiment_log", "deliverables", "conclusion",
                "revision", "lease_id"} <= state_cols
        episode_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(episodes)").fetchall()
        }
        assert {"conclusion_type", "concluded_at"} <= episode_cols
        alloc_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(proposer_allocations)").fetchall()
        }
        assert {"state", "reopen_count"} <= alloc_cols
        conn.close()


def test_schema_migrates_legacy_lease_columns():
    """A pre-complete-research DB gains the new columns with NULL defaults."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.db"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY, parent_node_id TEXT,
                experiment_id TEXT, sha TEXT UNIQUE NOT NULL,
                metrics TEXT NOT NULL DEFAULT '{}',
                gate_result TEXT NOT NULL DEFAULT '{}',
                depth INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL
            );
            CREATE TABLE episodes (
                episode_id TEXT PRIMARY KEY,
                inherited_from_episode_id TEXT, node_id TEXT NOT NULL,
                variation_operator TEXT, created_at REAL NOT NULL,
                last_active_at REAL NOT NULL
            );
            CREATE TABLE research_states (
                research_state_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                derived_from_research_state_id TEXT, transformation_id TEXT,
                working_model TEXT NOT NULL,
                evidence_refs TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            );
            CREATE TABLE proposals (
                proposal_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                episode_id TEXT NOT NULL, instruction TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE proposer_allocations (
                allocation_id TEXT PRIMARY KEY, node_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                reserved_proposal_ids TEXT NOT NULL DEFAULT '[]',
                started_at REAL NOT NULL, finished_at REAL,
                proposals_produced INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO nodes VALUES
                ('n1', NULL, NULL, 'sha1', '{}', '{}', 0, 'active', 1.0);
            INSERT INTO episodes VALUES
                ('e1', NULL, 'n1', NULL, 1.0, 1.0);
            INSERT INTO research_states VALUES
                ('rs-e1-001', 'n1', 'e1', NULL, NULL, 'memo', '[]', 1.0);
            INSERT INTO proposer_allocations VALUES
                ('a1', 'n1', 'e1', '[]', 1.0, NULL, 0);
            """
        )

        ResearchDBSchema.apply(conn)

        # Old rows survive with NULL new columns; the lease reads as
        # researching via COALESCE.
        row = conn.execute(
            "SELECT COALESCE(state, 'researching'), reopen_count "
            "FROM proposer_allocations WHERE allocation_id = 'a1'"
        ).fetchone()
        assert row[0] == "researching"
        assert row[1] == 0
        cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(research_states)").fetchall()
        }
        assert "lease_id" in cols
        conn.close()


def test_schema_migrates_legacy_proposal_table():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.db"
        conn = sqlite3.connect(str(path))
        conn.execute(
            """
            CREATE TABLE proposals (
                proposal_id TEXT PRIMARY KEY,
                node_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                instruction TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )

        ResearchDBSchema.apply(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()
        }

        assert "research_state_id" in columns
        assert "research_operation" in columns
        assert "donor_experiment_ids" in columns
        conn.close()
