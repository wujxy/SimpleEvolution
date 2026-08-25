"""Proposer-owned research-method library."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchSkill:
    skill_id: str
    description: str
    filename: str
    # Loaded in full into the system prompt at wake-up instead of on
    # demand.  claude_use teaches the seat-assistant relationship — it
    # must be present before the first decision, not fetched after the
    # seat has already forgotten its assistant exists (科学家完整研究制
    # §8.2: 技能开局加载).
    always_load: bool = False


_SKILLS = (
    ResearchSkill(
        "reframe_inherited_problem",
        "Rebuild a Child world's question from current facts instead of "
        "continuing the predecessor's memo.",
        "reframe_inherited_problem.md",
    ),
    ResearchSkill(
        "claude_use",
        "Working with your assistant — ask, debate, delegate execution, "
        "review.",
        "claude_use.md",
        always_load=True,
    ),
)
_BY_ID = {skill.skill_id: skill for skill in _SKILLS}
_SKILL_DIR = Path(__file__).with_name("research_skills")


def render_research_skill_catalog() -> str:
    """Return the complete compact catalog; the Scientist chooses what to read."""
    return "\n".join(
        f"- {skill.skill_id}: {skill.description}" for skill in _SKILLS
    )


def render_startup_skills() -> str:
    """Full text of every always-load skill (the wake-up block)."""
    parts = [
        load_research_skill(skill.skill_id)
        for skill in _SKILLS if skill.always_load
    ]
    return "\n\n".join(parts)


def load_research_skill(skill_id: str) -> str:
    """Load one known method without executing its scientific judgment."""
    skill = _BY_ID.get(skill_id)
    if skill is None:
        raise ValueError(f"unknown research skill: {skill_id}")
    return (_SKILL_DIR / skill.filename).read_text(encoding="utf-8").strip()
