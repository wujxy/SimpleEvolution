from __future__ import annotations

from pathlib import Path

import pytest

from scientist.ui.reader import RunLayout


def test_discover_requires_world_scientist(tmp_path: Path):
    with pytest.raises(ValueError, match=r"world/.scientist"):
        RunLayout.discover(tmp_path)


def test_safe_metadata_is_an_output_whitelist(run_fixture):
    run_dir, _ = run_fixture
    metadata = RunLayout.discover(run_dir).safe_metadata()
    assert metadata == {
        "goal": "make reconstruction faster",
        "episode_id": "ep-7",
        "budget": {"steps": 3000, "wall_seconds": 604800},
    }
    rendered = repr(metadata)
    assert "TOP-SECRET" not in rendered
    assert "SECRET-TOKEN" not in rendered
    assert "secret-host" not in rendered


def test_source_path_rejects_escape(run_fixture):
    run_dir, _ = run_fixture
    layout = RunLayout.discover(run_dir)
    with pytest.raises(ValueError, match="outside selected run"):
        layout.source_path("../private-key")
