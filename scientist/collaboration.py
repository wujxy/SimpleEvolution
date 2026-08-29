"""Role contracts and context isolation for research-team engagements."""
from __future__ import annotations

import json


ROLE_NAMES = frozenset({"searcher", "proposer", "executor", "challenger"})

# An open-scope proposer receives the neutral evidence index inline. A
# long run accumulates hundreds of experiments; unbounded, that index
# would put a hundred-thousand-character payload into every fresh seat's
# opening prompt. The most RECENT rows ship; the rest are reachable
# through experiment_ids, which the PI selects deliberately.
_EVIDENCE_INDEX_MAX_ROWS = 100


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
            rows = evidence_index
            overflow = 0
            if len(rows) > _EVIDENCE_INDEX_MAX_ROWS:
                overflow = len(rows) - _EVIDENCE_INDEX_MAX_ROWS
                # rows are sorted by experiment id (ascending sequence):
                # keep the most RECENT — what was lately tried and found
                # is the ground a fresh proposal must not re-tread
                rows = rows[-_EVIDENCE_INDEX_MAX_ROWS:]
            index_text = _json(rows)
            if overflow:
                index_text += (
                    f"\n(the {overflow} oldest of {len(evidence_index)} "
                    "experiments are omitted; the Scientist can forward "
                    "any of them through experiment_ids)")
            sections.append(
                "Reconstruct opportunities from the goal, live world, and "
                "neutral evidence index below. You have intentionally not "
                "received the Scientist's current preference, selected "
                "experiment ids, or reasoning history.\n\nNeutral evidence "
                "index:\n" + index_text
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
        "Your private trajectory is not the Scientist's memory. Close the "
        "engagement with a concise report of conclusions, evidence, "
        "artifacts, uncertainty, and recommended follow-up, as the FINAL "
        "message, in exactly this fenced JSON block — the harness reads "
        "these fields and delivers them to the Scientist; prose outside "
        "the block is archived but not delivered:\n"
        "```json\n"
        "{\n"
        '  "report_digest": "<the report: what you established, with the '
        'numbers>",\n'
        '  "diff_summary": "<files changed in your workspace, if any>",\n'
        '  "metrics": {"<name>": "<value with units>"},\n'
        '  "evidence": ["<what backs each claim: run, file, source>"],\n'
        '  "artifacts": ["<paths this engagement produced>"],\n'
        '  "uncertainty": "<what remains uncertain>",\n'
        '  "recommended_follow_up": "<the single most valuable next step>"\n'
        "}\n"
        "```"
    )
    return "\n\n".join(sections)
