"""Safe, incremental reads from one selected Scientist run."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


_SPEC_KEYS = ("goal", "episode_id", "budget")


@dataclass(frozen=True)
class RunLayout:
    """Resolved filesystem boundary for one run."""

    run_dir: Path
    scientist_dir: Path

    @classmethod
    def discover(cls, run_dir: Path) -> "RunLayout":
        root = Path(run_dir).resolve()
        scientist = root / "world" / ".scientist"
        if not root.is_dir() or not scientist.is_dir():
            raise ValueError(
                f"RUN_DIR must contain readable world/.scientist: {root}")
        return cls(root, scientist)

    def safe_metadata(self) -> dict[str, object]:
        """Return only display-safe spec fields."""
        path = self.run_dir / "spec.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {key: loaded[key] for key in _SPEC_KEYS if key in loaded}

    def source_path(self, relative: str) -> Path:
        """Resolve a source path without allowing escape from this run."""
        candidate = (self.run_dir / relative).resolve()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise ValueError("source path is outside selected run")
        return candidate
