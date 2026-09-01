"""Proposer-owned research-method library."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResearchSkill:
    skill_id: str
    description: str
    filename: str
    # A method may be loaded into standing context when its semantics are
    # stable enough to belong there. Collaboration identity is defined by the
    # team constitution, not by an optional method.
    always_load: bool = False


_SKILLS = (
    ResearchSkill(
        "reframe_inherited_problem",
        "Rebuild an inherited question from current facts instead of "
        "continuing a predecessor's memo.",
        "reframe_inherited_problem.md",
    ),
    ResearchSkill(
        "claude_use",
        "Role-based collaboration: frame engagements, compare reports, and "
        "retain scientific judgment.",
        "claude_use.md",
    ),
    ResearchSkill(
        "delegation",
        "The craft of working through colleagues: goal briefs, watching, "
        "nudging, re-chartering at stalls, and the two ledgers — "
        "experiments and colleagues.",
        "delegation.md",
    ),
    ResearchSkill(
        "analogical_transfer",
        "Examine the current problem through structurally similar problems "
        "from distant domains, and map the mechanism back — changing the "
        "question rather than refining the answer.",
        "analogical_transfer.md",
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
