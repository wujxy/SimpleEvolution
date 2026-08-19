"""Tests for episode-based ScientistSession."""
from __future__ import annotations

import tempfile
from pathlib import Path

from proposer.scientist_session import ScientistSession


def test_load_or_create_for_episode():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        session = ScientistSession.load_or_create_for_episode(
            run_dir, "episode-abc", prompt_version="scientist-v6"
        )
        assert session.session_dir == run_dir / "episodes" / "episode-abc" / "session"
        assert session.meta.get("episode_id") == "episode-abc"
        assert session.meta.get("prompt_version") == "scientist-v6"

        # Re-load returns the same scientist_id.
        session2 = ScientistSession.load_or_create_for_episode(
            run_dir, "episode-abc", prompt_version="scientist-v6"
        )
        assert session2.scientist_id == session.scientist_id


def test_save_meta_with_node_info():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        session = ScientistSession.load_or_create_for_episode(
            run_dir, "episode-abc", prompt_version="scientist-v6"
        )
        session.save_meta(node_id="node-1", node_sha="sha123")
        assert session.meta["node_id"] == "node-1"
        assert session.meta["node_sha"] == "sha123"
