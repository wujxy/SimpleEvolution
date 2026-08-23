"""Variation-factor basis (生成元基 / lens basis).

A generator is a LENS: the identity a research seat is hired for (seat
design §2.2).  The basis is a static ``generator.json`` at the repo root;
each entry follows the three-part standard — 操作指令 (directive) /
负面禁令 (forbidden) / 提交自检 (self_check).  The Supervisor buys seats
naming a lens id; the harness validates the purchase (lineage dedup) and
stamps the seat episode's ``variation_operator``.
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
    """One lens in the basis."""

    id: str
    name: str
    description: str
    directive: str = ""
    forbidden: str = ""
    self_check: str = ""


def load_generator_basis(path: Path | None = None) -> list[Generator]:
    """Load the lens basis from ``path`` (default: repo-root generator.json).

    A missing or malformed file degrades to an empty basis (no supervisor
    seat purchase can then be validated — the run stays honest rather than
    guessing lenses).
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
        if not isinstance(generator_id, str) or not generator_id:
            continue
        directive = str(item.get("directive") or "")
        basis.append(
            Generator(
                id=generator_id,
                name=str(item.get("name_zh") or generator_id),
                description=str(item.get("description") or directive),
                directive=directive,
                forbidden=str(item.get("forbidden") or ""),
                self_check=str(item.get("self_check") or ""),
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
    """Sample up to ``k`` lenses not yet tried, uniformly at random.

    Untried = ids not in ``tried_ids``.  When none remain (or the basis is
    empty) returns ``[]`` so the caller degrades to no variation factor.
    """
    untried = [g for g in basis if g.id not in tried_ids]
    if not untried:
        return []
    rng = rng if rng is not None else random
    chosen = rng.sample(untried, min(k, len(untried)))
    return list(chosen)
