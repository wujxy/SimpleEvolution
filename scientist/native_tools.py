"""Provider-native tool surface for the in-world (入世) scientist.

When the scientist's body moves INTO the world (scientist 包闭合为 CLI，以
world 容器为进程边界), the JSON-in-prose protocol retires: the model speaks
structured tool calls natively, so the whole protocol-repair machinery
(invalid_json salvage, batch envelopes, DSML drift) has nothing to drift
from. This module owns that collapsed tool surface:

  local (executed in-container, no border to cross)
      bash / read_file / write_file          — the world IS the filesystem
  forwarded (executed by the host wrapper over RPC)
      searcher / proposer / executor / challenger
      research view, research-memory, and experiment-archive channels
  terminal (forwarded; ends the run)
      deliver_world / abstain

One voice across every surface: the reader is the principal investigator;
Searcher, Proposer, Executor, and Challenger are colleagues; the harness is
the mechanical environment. Tool descriptions say when each colleague is
the right one to open work with — the identity lives in the system prompt,
the usage semantics live here.
"""
from __future__ import annotations

import json

from .model import ModelReply


def _fn(name: str, description: str, properties: dict,
        required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


# --- local generic tools (the old A class, collapsed into the world) -------

BASH_TOOL = _fn(
    "bash",
    "Run a bounded shell command in the live workspace when direct "
    "inspection or a small discriminating probe requires it. Use your "
    "shell to stay grounded and audit decisive evidence; substantial "
    "implementation, debugging, and measurement campaigns normally belong "
    "to an Executor.",
    {
        "command": {"type": "string"},
        "workdir": {
            "type": "string",
            "description": "absolute directory to run in, in the "
                           "workspace or scratch root; remembered across "
                           "calls",
        },
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "description": "per-call override in seconds; when omitted the "
                           "world default applies, and an explicit value "
                           "may exceed it up to a hard 3600s ceiling",
        },
    },
    ["command"],
)

READ_FILE_TOOL = _fn(
    "read_file",
    "Read one file with line numbers (path absolute in the workspace, "
    "repo, or scratch root — World Contact and Evaluation names them; "
    "offset 1-based; limit default 400, max 2000).",
    {
        "path": {"type": "string"},
        "offset": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
    },
    ["path"],
)

WRITE_FILE_TOOL = _fn(
    "write_file",
    "Write a file with exactly this content (heredoc quoting in a shell "
    "command corrupts code — use this for scripts and notes). Scratch is "
    "freely writable; in the live workspace only the editable paths accept "
    "writes and the rest refuse at the filesystem.",
    {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    ["path", "content"],
)


# --- forwarded tools (the host wrapper executes these over RPC) ------------
#
# SimpleEvolution's supervisor carries its own JSON-protocol specs for the
# same colleagues (simpleevo/host/research_tools.py); this is the native
# surface, not a mirror of that one.

SEARCHER_TOOL = _fn(
    "searcher",
    "Open work with a fresh Searcher colleague on a factual question about "
    "what is already known — literature, precedent, or the code in this "
    "world. They investigate independently and report sources, findings, "
    "disagreements, and uncertainty. The call returns an acknowledgment; "
    "their report arrives as a later observation.",
    {
        "brief": {"type": "string"},
        "read": {"type": "string", "enum": ["none", "node", "lab"]},
        "experiment_ids": {
            "type": "array", "items": {"type": "string"},
        },
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "engagement time box in minutes; when omitted the "
                           "role default applies (searcher 60, executor 120, proposer/challenger/reviewer 180)",
        },
    },
    ["brief"],
)

PROPOSER_TOOL = _fn(
    "proposer",
    "Open work with a fresh Proposer colleague to look for explanations "
    "or research directions. scope=open receives the goal, the live "
    "world, and neutral experiment evidence — not your Current Research "
    "Judgment or prior reasoning; use it when the research question "
    "itself deserves reconsideration. scope=directed works inside a "
    "region you name.",
    {
        "brief": {"type": "string"},
        "scope": {"type": "string", "enum": ["open", "directed"]},
        "region": {"type": "string"},
        "experiment_ids": {
            "type": "array", "items": {"type": "string"},
        },
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "engagement time box in minutes; when omitted the "
                           "role default applies (searcher 60, executor 120, proposer/challenger/reviewer 180)",
        },
    },
    ["brief", "scope"],
)

EXECUTOR_TOOL = _fn(
    "executor",
    "Open work with a fresh Executor colleague for substantial "
    "implementation, debugging, measurement, or experiment work — anything "
    "beyond a small discriminating probe. Give research intent, "
    "constraints, and a definition of done; they own how the work is "
    "carried through. The call returns an acknowledgment and their "
    "attributable report arrives as an observation when they finish; "
    "continue_engagement resumes a finished Executor with their context "
    "and workspace intact.",
    {
        "brief": {"type": "string"},
        "definition_of_done": {"type": "string"},
        "workspace": {
            "type": "string", "enum": ["current", "isolated"],
            "description": "where the engagement works: isolated gives "
                           "the colleague a disposable copy of the world "
                           "as a bench of their own — concurrent "
                           "engagements each get one, and the report "
                           "carries a diff the Scientist applies; "
                           "current (the default) works directly in the "
                           "live tree the Scientist shares",
        },
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
        },
    },
    ["brief", "definition_of_done"],
)

CONTINUE_ENGAGEMENT_TOOL = _fn(
    "continue_engagement",
    "Resume a finished Executor engagement — the same colleague, session "
    "context, and workspace — with a new brief. What changed in the world "
    "since they worked is part of the brief. Executor engagements only; "
    "the other roles open fresh each time.",
    {
        "collaborator_id": {
            "type": "string",
            "description": "the finished executor engagement to resume "
                           "(the id in its report)",
        },
        "brief": {"type": "string"},
        "definition_of_done": {"type": "string"},
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "engagement time box in minutes; when omitted "
                           "the executor default (120) applies",
        },
    },
    ["collaborator_id", "brief", "definition_of_done"],
)

WAIT_TOOL = _fn(
    "wait",
    "Block until pending collaborator engagements finish; their reports "
    "return as this call's result. With nothing pending, returns an empty "
    "status immediately. Engagements still running at the wait's bound "
    "keep running, and their reports arrive as observations in later "
    "turns.",
    {
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "bound on this wait in minutes; when omitted "
                           "the wait runs until every pending engagement "
                           "finishes (each within its own time box)",
        },
    },
    [],
)

CHALLENGER_TOOL = _fn(
    "challenger",
    "Open work with a fresh Challenger colleague to attack the current "
    "judgment or a specific claim — counterexamples, hidden assumptions, "
    "rival explanations, discriminating tests. Give the claim, its "
    "evidence, and the important uncertainty.",
    {
        "brief": {"type": "string"},
        "experiment_ids": {
            "type": "array", "items": {"type": "string"},
        },
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "engagement time box in minutes; when omitted the "
                           "role default applies (searcher 60, executor 120, proposer/challenger/reviewer 180)",
        },
    },
    ["brief"],
)

REVISE_RESEARCH_STATE_TOOL = _fn(
    "revise_research_state",
    "Rewrite your Current Research View — the one page of how you "
    "understand the problem now — at a real research junction where your "
    "working understanding, decisive evidence, or key uncertainty "
    "materially changes. The new view replaces the old one in your "
    "active context; prior versions remain reachable through the "
    "research-record channels. memory_updates, when given, records "
    "research-memory items in the same act (same fields as remember).",
    {
        "view": {"type": "string"},
        "revision_reason": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "memory_updates": {
            "type": "array",
            "description": "research-memory writes to make alongside the "
                           "view rewrite (same fields as remember)",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "existing item to update; omit to "
                                       "record a new one",
                    },
                    "content": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["active", "parked", "closed"],
                    },
                    "park_reason": {"type": "string"},
                    "close_scope": {"type": "string"},
                    "evidence_refs": {
                        "type": "array", "items": {"type": "string"},
                    },
                    "note": {"type": "string"},
                    "kind": {"type": "string"},
                },
            },
        },
    },
    ["view", "revision_reason"],
)

REMEMBER_TOOL = _fn(
    "remember",
    "Your long-term research memory: one call records a new item — a "
    "recognition, a direction, a result, an open question — or updates "
    "and re-statuses an existing one. Items persist for the whole run, "
    "independently of the view in your context and of compaction, and "
    "are searchable at any time (search_research_memory / "
    "list_research_memory / inspect_research_item).",
    {
        "item_id": {
            "type": "string",
            "description": "existing item to update; omit to record a "
                           "new one",
        },
        "content": {
            "type": "string",
            "description": "the item, in your own words, stated so it "
                           "can be found again",
        },
        "status": {
            "type": "string", "enum": ["active", "parked", "closed"],
            "description": "whether it deserves continued attention; "
                           "parking carries park_reason, closing carries "
                           "close_scope",
        },
        "park_reason": {
            "type": "string",
            "description": "required with status=parked — why it is set "
                           "aside",
        },
        "close_scope": {
            "type": "string",
            "description": "required with status=closed — exactly what "
                           "was tested and found dead, not just the "
                           "direction's name",
        },
        "evidence_refs": {
            "type": "array", "items": {"type": "string"},
            "description": "what backs it: call ids, experiment ids, "
                           "file paths",
        },
        "note": {
            "type": "string",
            "description": "optional annotation — relations to other "
                           "items, caveats",
        },
        "kind": {
            "type": "string",
            "description": "optional free tag, e.g. finding / question / "
                           "idea",
        },
    },
    [],
)

SEARCH_RESEARCH_MEMORY_TOOL = _fn(
    "search_research_memory",
    "Free-text search over your long-term research memory (every term "
    "must appear). Returns matching items with status and evidence "
    "references.",
    {
        "query": {"type": "string"},
        "status": {"type": "string", "enum": ["active", "parked", "closed"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    },
    ["query"],
)

LIST_RESEARCH_MEMORY_TOOL = _fn(
    "list_research_memory",
    "Thin index of your research-memory items, most recently touched "
    "first; inspect one deliberately for its full history.",
    {
        "status": {"type": "string", "enum": ["active", "parked", "closed"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
    [],
)

INSPECT_RESEARCH_ITEM_TOOL = _fn(
    "inspect_research_item",
    "One research-memory item in full — content, status, evidence, and "
    "its event history (park reasons, close scopes, revisions).",
    {"item_id": {"type": "string"}},
    ["item_id"],
)

LIST_RESEARCH_JUDGMENTS_TOOL = _fn(
    "list_research_judgments",
    "List a thin newest-first index of prior research-judgment revisions. "
    "Bodies are omitted; inspect one deliberately when its history matters.",
    {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
    [],
)

INSPECT_RESEARCH_JUDGMENT_TOOL = _fn(
    "inspect_research_judgment",
    "Read one historical research view (a past research-state revision) "
    "in full.",
    {"judgment_id": {"type": "string"}},
    ["judgment_id"],
)

SEARCH_EXPERIMENTS_TOOL = _fn(
    "search_experiments",
    "Coverage query over past experiments — check whether ground you are "
    "considering is already covered, and where the gaps are. Returns "
    "coverage rows only (no proposal or eval text).",
    {
        "query": {"type": "string"},
        "filters": {
            "type": "object",
            "properties": {
                "gate_passed": {"type": "boolean"},
                "changed_path": {"type": "string"},
                "status": {"type": "string"},
            },
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "buckets": {"type": "boolean"},
    },
    ["query"],
)

INSPECT_EXPERIMENT_TOOL = _fn(
    "inspect_experiment",
    "One experiment in full detail — proposal, status, gates, metrics, "
    "before/after revisions. The only channel that returns a proposal's "
    "text; the deliberate, one-at-a-time way to understand a past outcome.",
    {"experiment_id": {"type": "string"}},
    ["experiment_id"],
)

INSPECT_ORIGINATING_TOOL = _fn(
    "inspect_originating_research_state",
    "After inspecting one experiment, read the research judgment that "
    "originated it.",
    {"experiment_id": {"type": "string"}},
    ["experiment_id"],
)

USE_RESEARCH_SKILL_TOOL = _fn(
    "use_research_skill",
    "Load one optional research method when a different way of examining "
    "the research situation may be useful. Guidance only; all scientific "
    "judgment stays yours.",
    {"skill_id": {"type": "string"}},
    ["skill_id"],
)


# --- terminal tools (the exit contract as tools) ---------------------------

DELIVER_WORLD_TOOL = _fn(
    "deliver_world",
    "Deliver the current world as the research result, when you judge it "
    "contains a defensible result under the stated objective and "
    "constraints. Self-verify against the gates before delivering — a "
    "world that fails your own verification is a wasted delivery. The "
    "handover is a compact account (≤400 words) of what was established, "
    "the decisive evidence, and the important unresolved questions. A "
    "deliver is accepted only after a Reviewer has looked back at the "
    "state you are delivering — a listening requirement, not an "
    "approval: after hearing the report, the decision is entirely "
    "yours.",
    {
        "handover": {
            "type": "object",
            "properties": {
                "dead_ends": {
                    "type": "array", "items": {"type": "string"},
                    "description": "direction tried, evidence it is spent",
                },
                "open_questions": {
                    "type": "array", "items": {"type": "string"},
                    "description": "what remains open — including "
                                   "mechanisms you built but did NOT "
                                   "deliver, with their self-tested "
                                   "numbers",
                },
                "warning": {
                    "type": "string",
                    "description": "your strongest warning to a successor "
                                   "who may see the problem differently",
                },
            },
            "required": ["dead_ends", "open_questions", "warning"],
        },
    },
    ["handover"],
)

REVIEWER_TOOL = _fn(
    "reviewer",
    "Report your work to a fresh Reviewer for an advisory look-back. "
    "The Reviewer digs freely through the workspace and the full run "
    "record (.scientist/ — wire, judgments, collaborator reports) and "
    "returns its own read: does the work hold up, and what is still "
    "worth digging into. Advisory only — the judgment stays yours. "
    "Natural at three moments: after a major campaign or milestone "
    "(is the framing right? what is being missed?), when you doubt "
    "your direction, and before deliver_world — which is accepted "
    "only after a Reviewer has looked back at the state being "
    "delivered.",
    {
        "brief": {
            "type": "string",
            "description": "your report of the work: what you set out "
                           "to do, what you established, where you stand",
        },
        "experiment_ids": {
            "type": "array", "items": {"type": "string"},
        },
        "timeout_minutes": {
            "type": "integer", "minimum": 1, "maximum": 480,
            "description": "engagement time box in minutes; when omitted "
                           "the role default applies (searcher 60, "
                           "executor 120, proposer/challenger/reviewer 180)",
        },
    },
    ["brief"],
)


ABSTAIN_TOOL = _fn(
    "abstain",
    "Conclude the run without delivering. Use only when you judge that "
    "the available evidence does not support a defensible result and that "
    "no remaining inquiry merits further effort. A stable Current "
    "Research Judgment is not required.",
    {
        "reason": {"type": "string"},
        "axes_checked": {
            "type": "array", "items": {"type": "string"},
            "description": "axis: why empty",
        },
    },
    ["reason", "axes_checked"],
)


NOTE_TOOL = _fn(
    "note",
    "Append a short factual or working note worth preserving across "
    "compaction or resume — observations, references, measurements, small "
    "reminders. Notes are a research log, not your Current Research "
    "Judgment.",
    {"text": {"type": "string"}},
    ["text"],
)


NATIVE_TOOLS: tuple[dict, ...] = (
    # Order is the priority declaration: colleagues first, the view and
    # memory rhythm second, own eyes after, records and exits last.
    SEARCHER_TOOL,
    PROPOSER_TOOL,
    EXECUTOR_TOOL,
    CONTINUE_ENGAGEMENT_TOOL,
    CHALLENGER_TOOL,
    REVIEWER_TOOL,
    WAIT_TOOL,
    REVISE_RESEARCH_STATE_TOOL,
    REMEMBER_TOOL,
    NOTE_TOOL,
    READ_FILE_TOOL,
    BASH_TOOL,
    WRITE_FILE_TOOL,
    SEARCH_EXPERIMENTS_TOOL,
    INSPECT_EXPERIMENT_TOOL,
    INSPECT_ORIGINATING_TOOL,
    SEARCH_RESEARCH_MEMORY_TOOL,
    LIST_RESEARCH_MEMORY_TOOL,
    INSPECT_RESEARCH_ITEM_TOOL,
    LIST_RESEARCH_JUDGMENTS_TOOL,
    INSPECT_RESEARCH_JUDGMENT_TOOL,
    USE_RESEARCH_SKILL_TOOL,
    DELIVER_WORLD_TOOL,
    ABSTAIN_TOOL,
)

NATIVE_TOOL_NAMES = frozenset(t["function"]["name"] for t in NATIVE_TOOLS)

# Where each action executes. LOCAL = in-container (the world is the
# filesystem); FORWARDED = over the RPC channel to the host wrapper (the
# ledger, the experiment archive, and the assistant all live on the host);
# TERMINAL = forwarded, and ends the lease.
NATIVE_LOCAL_ACTIONS = frozenset(
    {"bash", "read_file", "write_file", "note"})
NATIVE_FORWARDED_ACTIONS = frozenset({
    "searcher", "proposer", "executor", "challenger", "reviewer",
    "continue_engagement", "wait",
    "revise_research_state", "remember", "search_experiments",
    "inspect_experiment", "inspect_originating_research_state",
    "search_research_memory", "list_research_memory",
    "inspect_research_item",
    "list_research_judgments", "inspect_research_judgment",
    "use_research_skill",
})
NATIVE_TERMINAL_ACTIONS = frozenset({"deliver_world", "abstain"})

# Tool calls the wrapper is allowed to serve (B/C class). Terminals arrive
# as conclusions, not tool requests.
FORWARDABLE_ACTIONS = NATIVE_FORWARDED_ACTIONS


# --- prompt blocks for the native (in-world) mode --------------------------
#
# The per-tool specs travel in the API payload now; the system prompt keeps
# only what is NOT a spec: the priority declaration (identity-level), the
# surviving protocol content, and the boundaries of the world the body
# lives in.

NATIVE_RUNTIME_BLOCK = """# Working with the Team

The collaborator functions attached to this conversation are how you open
work with Searcher, Proposer, Executor, Challenger, and Reviewer. The functions are a
communication mechanism; the collaborators are members of your research
team.

A Reviewer is not an exit-only ritual — a look-back at a milestone,
while there is still budget to act on what it finds, is often the
most valuable one.

A searcher, proposer, executor, or challenger engagement returns
immediately with an acknowledgment — the colleague works in their own
workspace and their attributable report arrives as an observation at the
start of one of your later turns; wait collects pending reports. A
Reviewer engagement runs inside the call: its report is the call's own
result, and while it reads your work you are listening — nothing else
until you have heard it through.

Independent hypotheses open as separate seats in one turn and run
concurrently — the time they spend is yours to think in. Reliability
first, efficiency second: parallel independent verification is
efficiency; cutting verification is the betrayal.

Each fresh engagement is a fresh collaborator. They may work through a
long internal trajectory of searching, reading, coding, debugging, or
measurement, but that private trajectory does not become your memory. What
returns to you is an attributable report: what was found or built, the
evidence and artifacts behind it, and what remains uncertain.
continue_engagement resumes a finished Executor with their context and
workspace intact; what changed in the world since they worked is part of
your brief to write.

A report is testimony from a colleague. Read it critically; inspect
decisive evidence yourself when the decision matters. Agreement, confident
prose, or a completed artifact is not proof.
"""

# Temporary compatibility name for imports outside the PI prompt assembler.
NATIVE_TOOL_BLOCK = NATIVE_RUNTIME_BLOCK

NATIVE_CONCLUDING_BLOCK = """# Concluding the Research Run

Use deliver_world when you judge that the current world contains a
defensible research result under the stated objective and constraints.
Self-verify against the gates before delivering; the handover briefly
explains what was established, the decisive evidence, and the important
questions that remain. Before you close, report your work to a
Reviewer and hear the read — the door accepts a deliver only after
that look-back, and it is advice, not a veto: the decision remains
yours.

Use abstain when you judge that the available evidence does not support a
defensible research result and that no remaining inquiry merits further
effort. A stable Current Research Judgment is not required to conclude
either way.

The harness may end the run externally at any point; such a run is
recorded as cut_off: the research record survives, but no scientific
conclusion is invented on your behalf.
"""

NATIVE_PROTOCOL_BLOCK = """# Runtime Mechanics

- Call tools directly, without a prose envelope. You may call several in
  one turn; they run in order and each answer returns to you. Plain text
  alongside a call is your own trajectory note — not a substitute for
  acting.
- A searcher/proposer/executor/challenger call returns an acknowledgment
  immediately; the attributable report arrives as a later observation (a
  killed or crashed engagement's salvaged partial report arrives the same
  way, marked as such). A reviewer call returns the report itself — it
  runs inside the call. wait blocks until pending engagements finish and
  returns their reports.
- revise_research_state replaces the current view note in your active
  context in place; your long-term research memory (remember and its
  search tools) is persistent and independent of that page.
- The terminal tools are exactly deliver_world and abstain; one of them
  concludes the run.
"""

_BOUNDARIES_TEMPLATE = """# World Contact and Evaluation

You can inspect the live world directly and run small probes when they
help you understand or audit the research. Direct inspection, small
discriminating probes, and independent checks are appropriate PI work.
Production implementation, long debugging, and measurement campaigns
should normally be carried by an Executor, so they do not consume the
context your judgment needs.

The world on disk: {work} is the live workspace — only its editable paths
accept writes; everything else in the tree is visible read-only.
{scratch} is temporary writable space. Git history of prior experiments
is readable through {repo} (`git show/diff/log`).

Measurements you make yourself are provisional evidence: they may guide
the research, and you should make them when they sharpen a decision. The
harness gates and the exit evaluation are authoritative for whether a
change is accepted and for its official score.

The harness is part of the environment, not a research collaborator. It
applies the fixed evaluation contract; it does not decide what results
mean or which direction the research should take.
"""


def render_native_boundaries(work: str, repo: str, scratch: str) -> str:
    """The boundaries block, truthful about THIS world's real roots.

    In-container the roots ARE ``/work`` / ``/repo`` / ``/scratch`` (the
    rendered text is then the classic one); standalone the roots are the
    host directories, and the shell must be told the paths that actually
    exist. Tool params accept either spelling (world._normalize_path).
    """
    return _BOUNDARIES_TEMPLATE.format(
        work=work, repo=repo, scratch=scratch,
    ).strip()


NATIVE_BOUNDARIES = render_native_boundaries("/work", "/repo", "/scratch")


def native_actions(reply: ModelReply) -> list[dict]:
    """Convert a native reply's tool calls to action dicts.

    ``{"action": name, **arguments}`` — the same shape the JSON protocol
    produced, so downstream dispatch/validation is unchanged. An
    unparsable-arguments call (provider bug) yields ``{"action": name,
    "_arguments_raw": ...}`` so the loop can reject it with the bytes
    visible."""
    actions: list[dict] = []
    for call in reply.tool_calls:
        action: dict = {"action": call.name, "tool_call_id": call.id}
        if call.arguments is None:
            action["_arguments_raw"] = call.arguments_raw
        else:
            action.update(call.arguments)
        actions.append(action)
    return actions


def wire_assistant_message(reply: ModelReply, actions: list[dict]) -> dict:
    """The assistant turn in OpenAI wire format (for the messages list).

    ``reasoning_content`` rides along when the provider returned one:
    thinking-mode APIs (DeepSeek) require it on replayed assistant turns
    in multi-turn tool loops — dropping it 400s the run intermittently
    (a live arm died at step 32 before this).
    """
    message = {
        "role": "assistant",
        "content": reply.text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_raw or "{}",
                },
            }
            for call in reply.tool_calls
        ],
    }
    if reply.reasoning:
        # Blank receipt: the validator wants the KEY present on turns
        # that thought; the content never enters the replayed context
        # (zero inflation — verified live that "" passes where absence
        # 400s). The verbatim reasoning is not part of the record.
        message["reasoning_content"] = ""
    return message


def wire_tool_result(tool_call_id: str, observation: dict) -> dict:
    """One tool observation in OpenAI wire format."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(observation, ensure_ascii=False),
    }
