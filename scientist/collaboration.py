"""Role contracts and context isolation for research-team engagements."""
from __future__ import annotations

import json


ROLE_NAMES = frozenset({"searcher", "proposer", "executor", "challenger"})


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _objective_experiments(experiments: list[dict]) -> str:
    return _json([
        {
            "experiment_id": item.get("experiment_id"),
            "intervention": item.get("intervention"),
            "observation": item.get("observation"),
        }
        for item in experiments
    ])


def build_collaboration_prompt(
    role: str,
    action: dict,
    *,
    goal: str,
    gate_block: str,
    current_judgment: dict | None,
    evidence_index: list[dict],
    selected_experiments: list[dict],
) -> str:
    """Render only the context allowed by one role's mandate."""
    if role not in ROLE_NAMES:
        raise ValueError(f"unknown collaborator role: {role}")
    brief = str(action.get("brief") or "").strip()
    if not brief:
        raise ValueError(f"{role}.brief must be non-empty")

    sections = [
        f"You are a fresh {role.title()} collaborator in a research team.",
        "Own this engagement: plan and carry out the investigation yourself, "
        "challenge the brief when evidence requires it, and return your own "
        "attributable research report.",
        f"Research goal:\n{goal}",
        f"Hard constraints:\n{gate_block}",
        f"Engagement brief:\n{brief}",
    ]
    if selected_experiments:
        sections.append(
            "Selected objective experiment observations:\n"
            + _objective_experiments(selected_experiments)
        )

    if role == "proposer":
        scope = str(action.get("scope") or "")
        if scope not in {"open", "directed"}:
            raise ValueError("proposer.scope must be open|directed")
        sections.append(f"Proposal scope: {scope}")
        if scope == "directed":
            region = str(action.get("region") or "").strip()
            if not region:
                raise ValueError("directed proposer requires region")
            sections.append(f"Directed research region:\n{region}")
        else:
            sections.append(
                "Reconstruct opportunities from the goal, live world, and "
                "neutral evidence index below. You have intentionally not "
                "received the Scientist's current preference, selected "
                "experiment ids, or reasoning history.\n\nNeutral evidence "
                "index:\n" + _json(evidence_index)
            )
    elif role == "challenger":
        sections.append(
            "Judgment to attack:\n"
            + _json(current_judgment or {
                "status": "no stable current judgment",
            })
        )
    elif role == "executor":
        done = str(action.get("definition_of_done") or "").strip()
        if not done:
            raise ValueError("executor.definition_of_done must be non-empty")
        sections.append(f"Definition of done:\n{done}")

    sections.append(
        "Your private trajectory is not the Scientist's memory. Return only "
        "a concise report of conclusions, evidence, artifacts, uncertainty, "
        "and recommended follow-up."
    )
    return "\n\n".join(sections)
