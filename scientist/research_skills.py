"""Skill library, Agent-Skills standard layout.

Each skill is a directory scientist/skills/<name>/SKILL.md with YAML
frontmatter (name, description, tier, audience, and optionally
always-load); the body is plain markdown. The format is the common
Claude Code / Codex skill spec, so a skill file is reusable by any
tool that speaks it — our loader is one consumer, not the owner.

Tiers: program (Scientist-only governance), task (how a kind of
research runs), research (craft of the work), discovery (how to make
genuinely new ground when the repertoire has no answer). Audience
records the sharing rule — research method belongs to every
researcher; program governance belongs to the Scientist — which the
seat-side channel will read.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SKILL_ROOT = Path(__file__).with_name("skills")


@dataclass(frozen=True)
class ResearchSkill:
    name: str
    description: str
    tier: str = "research"
    audience: str = "shared"
    always_load: bool = False
    directory: str = ""


def _parse_frontmatter(text: str) -> dict:
    """Flat YAML subset: `key: value` lines, folded `>` scalars."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    meta: dict[str, str] = {}
    key: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if key is not None:
            meta[key] = " ".join(part for part in buf if part).strip()

    for line in lines[1:end]:
        if line[:1] not in (" ", "\t") and ":" in line:
            flush()
            head, _, rest = line.partition(":")
            key = head.strip()
            rest = rest.strip()
            buf = [rest] if rest and rest not in (">", ">-", ">+",
                                                  "|", "|-", "|+") else []
        elif key is not None and line.strip():
            buf.append(line.strip())
    flush()
    return meta


def _load_all() -> tuple[ResearchSkill, ...]:
    skills: list[ResearchSkill] = []
    for skill_md in sorted(_SKILL_ROOT.glob("*/SKILL.md")):
        meta = _parse_frontmatter(
            skill_md.read_text(encoding="utf-8"))
        if not meta.get("name") or not meta.get("description"):
            raise ValueError(f"skill missing name/description: {skill_md}")
        skills.append(ResearchSkill(
            name=meta["name"],
            description=meta["description"],
            tier=meta.get("tier", "research"),
            audience=meta.get("audience", "shared"),
            always_load=meta.get("always-load", "").lower() == "true",
            directory=skill_md.parent.name,
        ))
    return tuple(skills)


_SKILLS = _load_all()
_BY_ID = {skill.name: skill for skill in _SKILLS}

_TIER_ORDER = {"task": 0, "research": 1, "discovery": 2}
_TIER_HEADER = {
    "task": "How a kind of research runs:",
    "research": "The craft of the work:",
    "discovery": "When the repertoire has no answer — how to make "
                 "genuinely new ground:",
}


def render_research_skill_catalog() -> str:
    """The resident index: tier-grouped, each skill with its moment."""
    lines: list[str] = []
    by_tier: dict[str, list[ResearchSkill]] = {}
    for skill in _SKILLS:
        if skill.always_load:
            continue  # resident already, via the wake-up block
        by_tier.setdefault(skill.tier, []).append(skill)
    for tier in sorted(by_tier, key=lambda t: _TIER_ORDER.get(t, 99)):
        lines.append(_TIER_HEADER.get(tier, f"{tier}:"))
        for skill in sorted(by_tier[tier], key=lambda s: s.name):
            lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


def _body(skill: ResearchSkill) -> str:
    """The skill's markdown body, frontmatter stripped."""
    text = (_SKILL_ROOT / skill.directory / "SKILL.md") \
        .read_text(encoding="utf-8").strip()
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            return text
        return "\n".join(lines[end + 1:]).strip()
    return text


def render_startup_skills() -> str:
    """Full text of every always-load skill (the wake-up block)."""
    return "\n\n".join(
        _body(skill) for skill in _SKILLS if skill.always_load)


def load_research_skill(skill_id: str) -> str:
    """Load one known method without executing its scientific judgment."""
    skill = _BY_ID.get(skill_id)
    if skill is None:
        raise ValueError(f"unknown research skill: {skill_id}")
    return _body(skill)


def install_shared_skills(target: Path) -> list[str]:
    """Install the shared-audience skills into a claude config dir.

    Seats are first-class researchers: research method belongs to
    every researcher; program governance (audience: scientist) stays
    with the Scientist. The claude CLI discovers personal skills at
    $CLAUDE_CONFIG_DIR/skills/<name>/SKILL.md — the same files the
    Scientist's use_research_skill serves, so PI and seats read one
    library, and a brief that names a method ("your line this
    engagement: representation-shift") points at a card the seat can
    open natively. Resident cost is the index only (name +
    description); bodies load on invocation.
    """
    import shutil
    skills_dir = target / "skills"
    installed = []
    for skill in _SKILLS:
        if skill.audience != "shared":
            continue
        shutil.copytree(_SKILL_ROOT / skill.directory,
                        skills_dir / skill.directory,
                        dirs_exist_ok=True)
        installed.append(skill.name)
    return installed
