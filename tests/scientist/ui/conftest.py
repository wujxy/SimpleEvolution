from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def run_fixture(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    scientist_dir = run_dir / "world" / ".scientist"
    (scientist_dir / "session").mkdir(parents=True)
    (run_dir / "spec.json").write_text(
        json.dumps({
            "goal": "make reconstruction faster",
            "episode_id": "ep-7",
            "budget": {"steps": 3000, "wall_seconds": 604800},
            "model": {
                "api_key": "TOP-SECRET",
                "base_url": "secret-host",
            },
            "assistant": {
                "env": {"ANTHROPIC_AUTH_TOKEN": "SECRET-TOKEN"},
            },
        }),
        encoding="utf-8",
    )
    return run_dir, scientist_dir
