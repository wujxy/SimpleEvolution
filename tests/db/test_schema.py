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
        }
        assert expected <= tables, f"missing tables: {expected - tables}"
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
        conn.close()
