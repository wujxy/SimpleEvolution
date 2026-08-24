"""Proposer-owned research-method library."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchSkill:
    skill_id: str
    description: str
    filename: str


_SKILLS = (
    ResearchSkill(
        "reframe_inherited_problem",
        "Rebuild a Child world's question from current facts instead of "
        "continuing the predecessor's memo.",
        "reframe_inherited_problem.md",
    ),
)
_BY_ID = {skill.skill_id: skill for skill in _SKILLS}
_SKILL_DIR = Path(__file__).with_name("research_skills")


def render_research_skill_catalog() -> str:
    """Return the complete compact catalog; the Scientist chooses what to read."""
    return "\n".join(
        f"- {skill.skill_id}: {skill.description}" for skill in _SKILLS
    )


def load_research_skill(skill_id: str) -> str:
    """Load one known method without executing its scientific judgment."""
    skill = _BY_ID.get(skill_id)
    if skill is None:
        raise ValueError(f"unknown research skill: {skill_id}")
    return (_SKILL_DIR / skill.filename).read_text(encoding="utf-8").strip()
