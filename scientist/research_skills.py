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
        "delegation",
        "Work is passing to a colleague, or one drifts or stalls: the "
        "craft of goal briefs, watching, re-chartering, and the two "
        "ledgers — experiments and colleagues.",
        "delegation.md",
    ),
    ResearchSkill(
        "reframe_inherited_problem",
        "The question arrived pre-answered by a predecessor's memo or "
        "framing: rebuild it from current facts instead of continuing "
        "the answer.",
        "reframe_inherited_problem.md",
    ),
    ResearchSkill(
        "analogical_transfer",
        "This mechanism has been seen somewhere else: map the distant "
        "structure back and change the question rather than refine the "
        "answer.",
        "analogical_transfer.md",
    ),
    ResearchSkill(
        "claude_use",
        "You are working through a colleague's interface: framing "
        "engagements, comparing reports, retaining scientific judgment.",
        "claude_use.md",
    ),
    ResearchSkill(
        "research_expansion",
        "The space has narrowed — every new idea is a variation of one "
        "framing, or a wall claim is about to become the reason to stop: "
        "compose genuinely different directions and open them together.",
        "research_expansion.md",
    ),
    ResearchSkill(
        "knowledge_gap_search",
        "Judgment is blocked on what you don't know, or one query keeps "
        "returning one kind of answer: decompose the unknown into "
        "distinct questions and search them in parallel.",
        "knowledge_gap_search.md",
    ),
    ResearchSkill(
        "consensus_independence_audit",
        "Every reading agrees — especially when it agrees too smoothly, "
        "or a conclusion arrives already endorsed: count independent "
        "channels, not signatures.",
        "consensus_independence_audit.md",
    ),
    ResearchSkill(
        "wall_foundation_attack",
        "You are about to claim a floor, ceiling, or impossibility: "
        "attack the reading of the constraint before the number under "
        "it.",
        "wall_foundation_attack.md",
    ),
    ResearchSkill(
        "cheapest_discriminating_experiment",
        "Two explanations both fit the evidence and the choice is live: "
        "find the cheapest observation they predict differently, and "
        "freeze predictions before looking.",
        "cheapest_discriminating_experiment.md",
    ),
    ResearchSkill(
        "assumption_audit",
        "A plan is formed and about to consume resources: list its "
        "load-bearing assumptions and check the dangerous-cheap ones "
        "first.",
        "assumption_audit.md",
    ),
    ResearchSkill(
        "measurement_discipline",
        "A number is about to be compared, banked, or acted on: make "
        "the instrument's noise floor explicit and the measurement tier "
        "appropriate.",
        "measurement_discipline.md",
    ),
    ResearchSkill(
        "critical_validation",
        "A result is about to become a conclusion — especially one "
        "surprisingly good, bad, or out of pattern: validate in "
        "proportion to the surprise.",
        "critical_validation.md",
    ),
    ResearchSkill(
        "hypothesis_generation_and_evolution",
        "Fresh ideas have stopped coming but candidates are on the "
        "table: evolve what you hold — recombine, simplify, invert, "
        "shift abstraction — instead of re-sampling.",
        "hypothesis_generation_and_evolution.md",
    ),
    ResearchSkill(
        "controls_and_confounders",
        "An effect is observed and about to be attributed to a cause: "
        "isolate it from its confounders with designed controls before "
        "explaining it.",
        "controls_and_confounders.md",
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
