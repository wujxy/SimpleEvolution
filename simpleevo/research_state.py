"""Shared immutable cognitive records for Research State evolution."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CognitiveTransformation:
    transformation_id: str
    node_id: str
    episode_id: str
    source_research_state_id: str | None
    operator_id: str
    challenge: str
    created_at: float


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


def transformation_to_dict(value: CognitiveTransformation) -> dict[str, Any]:
    return asdict(value)


def research_state_to_dict(value: ResearchState) -> dict[str, Any]:
    result = asdict(value)
    result["evidence_refs"] = list(value.evidence_refs)
    return result
