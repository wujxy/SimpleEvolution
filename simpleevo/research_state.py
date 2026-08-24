"""Shared cognitive records for Research State evolution.

Classic runs (and the integrator lane) publish immutable snapshot rows.
A complete-research lease instead keeps ONE evolving head row per lease
that the worker upserts every work cycle (revision+1); the six-block
columns carry the full-resolution record that successors reach only by
pull (科学家完整研究制 §2.5).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ResearchState:
    research_state_id: str
    node_id: str
    episode_id: str
    derived_from_research_state_id: str | None
    working_model: str
    evidence_refs: tuple[str, ...]
    created_at: float
    # Dormant since the seat design removed transform_worldview; kept
    # for schema stability (cognitive_transformations table preserved).
    transformation_id: str | None = None
    # Six-block structure (complete-research leases).  ``evidence`` is a
    # list of {claim, how, numbers, source, status: belief|verified}
    # entries; ``experiment_log`` accumulates one entry per work cycle
    # (intent -> sha -> self-test numbers -> verdict, failures included);
    # ``deliverables`` holds candidate SHAs; ``conclusion`` carries
    # {type, exhaustion, open_questions, handover, compliant}.
    evidence: tuple[dict[str, Any], ...] = ()
    experiment_log: tuple[dict[str, Any], ...] = ()
    deliverables: tuple[dict[str, Any], ...] = ()
    conclusion: dict[str, Any] | None = None
    revision: int | None = None
    lease_id: str | None = None


def research_state_to_dict(value: ResearchState) -> dict[str, Any]:
    result = asdict(value)
    result["evidence_refs"] = list(value.evidence_refs)
    result["evidence"] = list(value.evidence)
    result["experiment_log"] = list(value.experiment_log)
    result["deliverables"] = list(value.deliverables)
    return result
