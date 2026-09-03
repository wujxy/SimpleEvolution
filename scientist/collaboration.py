"""Role contracts and context isolation for research-team engagements."""
from __future__ import annotations

import json


ROLE_NAMES = frozenset({
    "searcher", "proposer", "executor", "challenger", "reviewer"})

# The seat's standing — its position in the team. The prompt channel
# (a claude session's first message) carries assignments and document
# priority, not identity: "You are X" arriving as a user message reads
# as a request to act, not as who the session is. So the station rides
# the mission as an engagement line, and the mission alone: a CLAUDE.md
# manual layer was built and then removed — the pure-mission probe
# (goal above, brief framed as one colleague's account, authority
# restated below it) already catches a brief that retells a tolerance
# as a hard law, and CLAUDE.md loads once at session start exactly as
# the mission does, so the extra channel bought no persistence and
# cost a cross-talk surface.
_SEAT_STATIONS = {
    "challenger": (
        "You are engaged as the team's Challenger — its skeptic, for "
        "whom the program's current beliefs arrive as claims to test."
    ),
    "reviewer": (
        "You are engaged as the team's Reviewer — its hindsight, "
        "reading the whole record against the claims made on it."
    ),
    "proposer": (
        "You are engaged as the team's Proposer — standing where the "
        "program is not currently looking."
    ),
    "searcher": (
        "You are engaged as the team's Searcher — its contact with "
        "facts that live outside this room."
    ),
    "executor": (
        "You are engaged as the team's Executor — the one who makes "
        "reality answer; inside the charter the loop is yours."
    ),
}

# An open-scope proposer receives the neutral evidence index inline. A
# long run accumulates hundreds of experiments; unbounded, that index
# would put a hundred-thousand-character payload into every fresh seat's
# opening prompt. The most RECENT rows ship; the rest are reachable
# through experiment_ids, which the PI selects deliberately.
_EVIDENCE_INDEX_MAX_ROWS = 100

# The lab's record discipline, riding every engagement prompt (and the
# continuation prompt) alike: it binds the seat and, by symmetry, the
# Scientist whose briefs the seat reads. The point is the evidence
# chain — a colleague's conclusion is citable, graded, and checkable,
# never something that travels only by retelling. r9 died of the
# opposite: one ungraded sentence relayed mouth-to-mouth became law
# for five hours with three endorsements and zero tests.
_LAB_RECORD_BLOCK = (
    "Laboratory record discipline — four rules, binding every member "
    "of this lab, the Scientist included:\n"
    "1. Conclusions live in the record, not in conversation. The "
    "report you close with is archived by the machinery into "
    "``.scientist/record.jsonl`` as a citable entry (REC-001, "
    "REC-002, …); nothing you establish travels further by "
    "retelling.\n"
    "2. Cite, don't retell. When your work rests on a colleague's "
    "conclusion, read the record entry itself — its grade, its "
    "evidence, its limits — not a summary of it. A forked bench "
    "carries no record: there, what a brief quotes of an entry — its "
    "id, grade, and falsifier — is the entry's face to you, and any "
    "restrictive sentence in a brief that cites neither the spec nor "
    "a record entry is one colleague's advice: checkable, and "
    "overridable by what you find.\n"
    "3. Verify what you build on. Before your next step rests on an "
    "entry graded ``inferred``, weigh the cost of running its "
    "falsifier — usually minutes — against the cost of being wrong; "
    "what the refutation yields is yours to report.\n"
    "4. ``not_measured`` is a result. An instrument that failed "
    "reports the failure; it never reports a conclusion."
)


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


# The closing report contract every engagement ends with. Shared by the
# fresh-seat prompt and the continuation prompt — a resumed session gets
# it restated so the final fenced JSON stays anchored.
_CLOSING_CONTRACT = (
    "Your private trajectory is not the Scientist's memory. Close the "
    "engagement with a concise report of conclusions, each with its "
    "grade, plus evidence, artifacts, uncertainty, and recommended "
    "follow-up, as the FINAL message, in exactly this fenced JSON "
    "block — the harness reads these fields, archives them in the lab "
    "record, and delivers them to the Scientist; prose outside the "
    "block is archived but not delivered:\n"
    "```json\n"
    "{\n"
    '  "report_digest": "<the report: what you established, with the '
    'numbers>",\n'
    '  "claim_grade": "<grade every conclusion you state: \\"measured\\" '
    '(a run or reading backs it — name it in evidence), '
    '\\"not_measured\\" (you tried to observe and the instrument '
    'failed — say what failed), or \\"inferred\\" (reasoned from other '
    'facts)>",\n'
    '  "falsifier": "<for inferred conclusions: the cheapest concrete '
    'experiment that would refute the claim — a change to try, a '
    'command to run; \\"n/a\\" when every stated conclusion is '
    'measured or not_measured>",\n'
    '  "diff_summary": "<files changed in your workspace, if any; for '
    'fork work: the commit range or diff against the base>",\n'
    '  "metrics": {"<name>": "<value with units>"},\n'
    '  "evidence": ["<what backs each claim: run, file, source>"],\n'
    '  "artifacts": ["<paths this engagement produced>"],\n'
    '  "uncertainty": "<what remains uncertain>",\n'
    '  "recommended_follow_up": "<the single most valuable next step>"\n'
    "}\n"
    "```"
)


def _fuse_note(fuse_seconds: int | None) -> str:
    """The seat's own runway, stated as fact. A worker that cannot see
    its fuse cannot pace to it — commit checkpoints, measurement passes,
    and the depth of a side investigation all price differently against
    fifteen minutes versus three hours. Information only: the fuse
    bounds the unwatched interval, never the work, and salvage (or a
    continuation) keeps what was laid down."""
    if not fuse_seconds:
        return ""
    return (f"Fuse: about {max(fuse_seconds, 0) // 60} minutes before "
            "the harness salvages — report, transcript, and session "
            "survive a salvage, and a continued engagement resumes "
            "this work.")


def build_collaboration_prompt(
    role: str,
    action: dict,
    *,
    goal: str,
    gate_block: str,
    current_judgment: dict | None,
    evidence_index: list[dict],
    selected_experiments: list[dict],
    fuse_seconds: int | None = None,
) -> str:
    """Render only the context allowed by one role's mandate."""
    if role not in ROLE_NAMES:
        raise ValueError(f"unknown collaborator role: {role}")
    brief = str(action.get("brief") or "").strip()
    if not brief:
        raise ValueError(f"{role}.brief must be non-empty")

    sections = [
        _SEAT_STATIONS[role],
        "Plan and carry out the investigation yourself and return your "
        "own attributable research report; the goal and hard "
        "constraints that follow are what your work answers to.",
        f"Research goal:\n{goal}",
        f"Hard constraints:\n{gate_block}",
        "You work inside the run's laboratory. Its instruments are sealed "
        "— the tests, the evaluation and the baseline are constants every "
        "claim answers to. Git is how knowledge accumulates in this lab: "
        "a commit is a result others can stand on, a diff is how a change "
        "travels between colleagues, and anything you need from the "
        "repository at an earlier state — a clean baseline, a comparison "
        "point — is one ``git worktree`` away. A ``.scientist/`` directory "
        "may sit in the tree: that is the run's own living record — "
        "memory, reports, correspondence — kept by the machinery. It is "
        "yours to read; it belongs to the run, not to the tree, and no "
        "git operation reaches it.",
        _LAB_RECORD_BLOCK,
        f"Engagement brief — the Scientist's account and request. Its "
        f"measured facts are yours to use; its characterizations — of "
        f"the constraints, of the terrain — are one colleague's "
        f"reading of the text above, and yours to check:\n{brief}",
        "Throughout this engagement, the goal and constraints stated "
        "above remain the authority; the brief's characterizations of "
        "them are one colleague's reading, and you hold the text.",
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
    elif role == "reviewer":
        # No judgment, no evidence index, no curated context — the
        # briefing is the claim, the live world and the run record are
        # the facts, and this colleague digs for itself.
        sections.append(
            "Mandate: look back over this research as a whole. The "
            "briefing you received is the Scientist's own account — a "
            "claim, not a fact. The live world and the full run record "
            "(the wire, views, research memory, collaborator reports — "
            "paths in your workspace note) are readable; verify the "
            "account against them, judge the work on its merits, and "
            "name what you would dig into next that the Scientist has "
            "not tried."
        )
    elif role == "executor":
        done = str(action.get("definition_of_done") or "").strip()
        if not done:
            raise ValueError("executor.definition_of_done must be non-empty")
        sections.append(f"Definition of done:\n{done}")
        if str(action.get("workspace") or "current") == "isolated":
            sections.append(
                "Workspace discipline: this engagement runs in a disposable "
                "copy of the world with its own copy of the git history — "
                "a bench of your own, and commits you make here stay here. "
                "Commit your work in that copy as you go, and report the "
                "change in ``diff_summary`` as the diff or commit range "
                "(HEAD against the base) — the Scientist applies and "
                "re-verifies it in the live world themselves."
            )
        else:
            sections.append(
                "Workspace: the live bench itself — the same tree the "
                "Scientist works in, and the run's shared git history. "
                "Your commits land in the world directly; they are what "
                "the next colleague inherits."
            )

    fuse = _fuse_note(fuse_seconds)
    if fuse:
        sections.append(fuse)
    sections.append(_CLOSING_CONTRACT)
    return "\n\n".join(sections)


def build_continuation_prompt(action: dict, *,
                              fuse_seconds: int | None = None) -> str:
    """Render the brief for resuming a finished Executor engagement.

    The resumed session already carries its own context — the codebase it
    read, the experiments it ran, the craft it accumulated. What it does
    not carry is anything that happened since; that is the PI's brief to
    write (the harness supplies none of it).
    """
    brief = str(action.get("brief") or "").strip()
    if not brief:
        raise ValueError("continue_engagement.brief must be non-empty")
    done = str(action.get("definition_of_done") or "").strip()
    if not done:
        raise ValueError("continue_engagement.definition_of_done must be "
                         "non-empty")
    sections = [
        _SEAT_STATIONS["executor"],
        "You are the same Executor collaborator, resumed: this engagement "
        "continues your prior session in your existing workspace — your "
        "context and your work are where you left them. What changed in "
        "the world since you worked is in the brief below — the "
        "Scientist's account, as ever; the goal and constraints as "
        "written still govern.",
        _LAB_RECORD_BLOCK,
        f"Engagement brief:\n{brief}",
        f"Definition of done:\n{done}",
    ]
    fuse = _fuse_note(fuse_seconds)
    if fuse:
        sections.append(fuse)
    sections.append(_CLOSING_CONTRACT)
    return "\n\n".join(sections)
