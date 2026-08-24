"""Prompt loading helpers for experiment roles."""
from __future__ import annotations

from pathlib import Path


def load_semantic(role: str, prompt_dir: Path | None = None) -> str:
    """Load the semantic prompt for a role, falling back to a minimal default."""
    if prompt_dir is not None:
        path = Path(prompt_dir) / f"{role}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    if role == "executor":
        return (
            "You are a careful coding agent. Implement the requested change "
            "in the worktree, run any local verification, and emit a SELF_REPORT "
            "block. Do not commit; the harness will commit for you."
        )
    return ""
