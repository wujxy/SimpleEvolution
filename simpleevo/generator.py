"""Variation-factor basis (生成元基).

A generator is a re-framing / mutation directive injected into a re-studied
Scientist episode (see ``episodes.variation_operator``).  The basis is a
static ``generator.json`` at the repo root; the harness samples untried
generators per node so each reseed is pointed at a fresh cognitive axis.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_DEFAULT_PATH = Path(__file__).resolve().parents[1] / "generator.json"


@dataclass(frozen=True)
class Generator:
    """One re-framing directive in the basis."""

    id: str
    name: str
    description: str


def load_generator_basis(path: Path | None = None) -> list[Generator]:
    """Load the generator basis from ``path`` (default: repo-root generator.json).

    A missing or malformed file degrades to an empty basis (reseed then runs
    with no variation factor, i.e. today's behavior).
    """
    target = Path(path) if path is not None else _DEFAULT_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    basis: list[Generator] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        generator_id = item.get("id")
        description = item.get("description")
        if not isinstance(generator_id, str) or not generator_id:
            continue
        basis.append(
            Generator(
                id=generator_id,
                name=str(item.get("name_zh") or generator_id),
                description=str(description or ""),
            )
        )
    return basis


def sample_generators(
    basis: Sequence[Generator],
    tried_ids: set[str] | frozenset[str],
    *,
    k: int = 2,
    rng: random.Random | None = None,
) -> list[Generator]:
    """Sample up to ``k`` generators not yet tried (per node).

    Untried = ids not in ``tried_ids``.  When none remain (or the basis is
    empty) returns ``[]`` so the caller degrades to no variation factor.
    """
    untried = [g for g in basis if g.id not in tried_ids]
    if not untried:
        return []
    rng = rng if rng is not None else random
    chosen = rng.sample(untried, min(k, len(untried)))
    return list(chosen)


def select_one_generator(
    basis: Sequence[Generator],
    tried_ids: set[str] | frozenset[str],
) -> Generator | None:
    """Return the first generator not yet used, or ``None`` when exhausted."""
    return next((item for item in basis if item.id not in tried_ids), None)
