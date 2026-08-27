"""SimpleEvolution worker prompt loading (the host-side generation).

The proposer charter (``proposer.md``) and the integrator/reflection/
self-review texts live here and travel with the supervisor jobs that
render them. A run may override any of them by passing ``prompt_dir``
(via the lane manifest); otherwise the packaged defaults are used.
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parent


def load_semantic(role: str = "proposer",
                  prompt_dir: str | Path | None = None) -> str:
    """Read ``{prompt_dir}/{role}.md`` (or this package's ``{role}.md`` when
    ``prompt_dir`` is None)."""
    root = Path(prompt_dir).resolve() if prompt_dir else _DEFAULT_DIR
    return (root / f"{role}.md").read_text(encoding="utf-8")
