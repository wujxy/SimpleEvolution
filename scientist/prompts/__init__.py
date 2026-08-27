"""Scientist prompt loading.

The PI charter (``scientist.md``) and the team/memory blocks
(``research_team.md``, ``research_memory.md``) live in this package and
travel with it. A spec may override the charter by passing ``charter``
directly; these files are the packaged defaults.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parent


def load_semantic(role: str,
                  prompt_dir: str | Path | None = None) -> str:
    """Read ``{prompt_dir}/{role}.md`` (or this package's ``{role}.md``
    when ``prompt_dir`` is None)."""
    root = Path(prompt_dir).resolve() if prompt_dir else _DEFAULT_DIR
    return (root / f"{role}.md").read_text(encoding="utf-8")
