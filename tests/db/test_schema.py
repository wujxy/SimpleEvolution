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
            "threads",
            "proposals",
            "experiments",
            "attempts",
            "frontier_axes",
            "proposer_allocations",
            "scheduler_events",
        }
        assert expected <= tables, f"missing tables: {expected - tables}"
        conn.close()
