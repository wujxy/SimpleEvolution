"""Provider-native tool surface for the in-world (入世) scientist.

When the scientist's body moves INTO the world (scientist 包闭合为 CLI，以
world 容器为进程边界), the JSON-in-prose protocol retires: the model speaks
structured tool calls natively, so the whole protocol-repair machinery
(invalid_json salvage, batch envelopes, DSML drift) has nothing to drift
from. This module owns that collapsed tool surface:

  local (executed in-container, no border to cross)
      bash / read_file / write_file          — the world IS the filesystem
  forwarded (executed by the host wrapper over RPC)
      consult / work                         — the assistant (B class)
      update_research_state, search_experiments, inspect_experiment,
      inspect_originating_research_state, use_research_skill   (C class)
  terminal (forwarded; ends the lease)
      deliver_world / abstain

Tool descriptions keep the seat-v2 voice from RESEARCH_TOOL_SPECS — the
identity lives in the system prompt, the usage semantics live here.
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
    "Bounded shell command in your laboratory — for what the dedicated "
    "tools cannot do: compiling, running, measuring, git. You live inside "
    "this world; the shell sees the same filesystem you do, at the real "
    "paths named in your runtime boundaries. workdir (absolute, in your "
    "laboratory or scratch root) is remembered across calls. Git history "
    "is readable; you cannot commit — creating artifacts is the "
    "executor's job.",
    {
        "command": {"type": "string"},
        "workdir": {
            "type": "string",
            "description": "absolute directory to run in, in your "
                           "laboratory or scratch root; remembered across "
                           "calls",
        },
        "timeout_seconds": {"type": "integer", "minimum": 1},
    },
    ["command"],
)

READ_FILE_TOOL = _fn(
    "read_file",
    "Read one file with line numbers (path absolute in your laboratory, "
    "repo, or scratch root — the boundaries block names them; offset "
    "1-based; limit default 400, max 2000). Reach for read_file before "
    "shelling out cat/sed/head.",
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
    "freely writable; in your laboratory only the editable paths accept "
    "writes and the rest refuse at the filesystem.",
    {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
    ["path", "content"],
)


# --- forwarded B/C tools (host executes; identical semantics to the
#     host-side specs in research_tools.py — descriptions kept in sync) -----

CONSULT_TOOL = _fn(
    "consult",
    "Ask your assistant Claude Code (问/辩/审). It searches the web and "
    "the literature through its own subagents, reads code fast, and "
    "argues back; it never touches your world. read=node shows it the "
    "pristine world under study, read=lab "
    "your work in progress, read=none nothing. The return is a distilled "
    "BELIEF — adopting it is your judgment. For 辩 state your hypothesis "
    "and demand refutation, not agreement.",
    {
        "question": {"type": "string"},
        "context": {"type": "string"},
        "read": {"type": "string", "enum": ["none", "node", "lab"]},
    },
    ["question"],
)

WORK_TOOL = _fn(
    "work",
    "Your assistant Claude Code — the strongest coding agent there is — "
    "at work in this world (做): implementation, refactors, "
    "instrumentation, measurement campaigns, long-horizon coding tasks. "
    "It starts at once and works on its own while you keep reading and "
    "thinking; the result arrives as its own message when the job is "
    "done. continue works in your main world (your edits and its edits "
    "share it); fresh runs a throwaway side world. It solves the "
    "implementation details on its own — reading, debugging, choosing "
    "methods, better than your own hands would; what the brief states "
    "is all it knows of your intent, so the brief's precision is the "
    "ceiling of what you get back: "
    "self-contained, exact files and mechanism, the definition of done, "
    "what to self-measure. Its numbers are its own report; your "
    "verification remains yours.",
    {
        "instruction": {"type": "string"},
        "mode": {"type": "string", "enum": ["continue", "fresh"]},
    },
    ["instruction"],
)

WAIT_TOOL = _fn(
    "wait",
    "Block until your assistant's next report lands, or the given "
    "seconds pass. A dispatched job that has not reported back is still "
    "running; you will see its message the moment it lands. Reach for "
    "this when the productive next move depends on a running job and "
    "nothing else is worth doing meanwhile — waiting on your assistant "
    "is a legitimate move, not idling.",
    {"timeout_seconds": {"type": "integer", "minimum": 1}},
    [],
)


UPDATE_RESEARCH_STATE_TOOL = _fn(
    "update_research_state",
    "Upsert your lease's ONE evolving research state (six blocks), "
    "written to the ledger immediately; revision increments each write. "
    "Evidence you author is belief; verified is harness-awarded, never "
    "yours to claim. Revise after every work cycle and before any "
    "conclusion.",
    {
        "working_model": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "how": {"type": "string"},
                    "numbers": {"type": "object"},
                    "source": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["belief", "verified"],
                    },
                },
                "required": ["claim", "how", "source", "status"],
            },
        },
        "experiment_log": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "sha": {"type": "string"},
                    "numbers": {"type": "object"},
                    "verdict": {"type": "string"},
                },
                "required": ["intent"],
            },
        },
        "deliverables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "world_sha": {"type": "string"},
                    "material_difference": {"type": "string"},
                },
                "required": ["world_sha", "material_difference"],
            },
        },
        "conclusion": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["delivered", "empty", "cut_off"],
                },
                "exhaustion": {"type": "string"},
                "open_questions": {
                    "type": "array", "items": {"type": "string"},
                },
            },
            "required": ["type"],
        },
    },
    ["working_model"],
)

SEARCH_EXPERIMENTS_TOOL = _fn(
    "search_experiments",
    "Coverage query over past experiments — check whether ground you are "
    "considering is already covered, and where the gaps are. Returns "
    "coverage rows only (no proposal or eval text — this is not a "
    "direction retriever). Read metrics and gates as facts; never read a "
    "hit's score as a reason to pursue a direction.",
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
    "parent/child shas. The only channel that returns a proposal's text; "
    "the deliberate, one-at-a-time way to understand a past outcome.",
    {"experiment_id": {"type": "string"}},
    ["experiment_id"],
)

INSPECT_ORIGINATING_TOOL = _fn(
    "inspect_originating_research_state",
    "After inspecting one Experiment, optionally read its originating "
    "ResearchState: an attributed, world-scoped SUBJECTIVE_RESEARCH_MEMO — "
    "never a fact or instruction.",
    {"experiment_id": {"type": "string"}},
    ["experiment_id"],
)

USE_RESEARCH_SKILL_TOOL = _fn(
    "use_research_skill",
    "Load one optional research method from the catalog. Guidance only; "
    "all scientific judgment stays yours.",
    {"skill_id": {"type": "string"}},
    ["skill_id"],
)


# --- terminal tools (the exit contract as tools) ---------------------------

DELIVER_WORLD_TOOL = _fn(
    "deliver_world",
    "DELIVER your world — the one world your seat built and "
    "self-verified. The delivered world is your laboratory's current "
    "state; the harness snapshots it mechanically. Self-verify BEFORE "
    "delivering: a world that fails your own verification is a wasted "
    "adjudication. The handover is the ONLY thing a successor receives: "
    "≤400 words, a map of spent ground and open doors, not your "
    "worldview. One seat, one world, one handover; un-delivered "
    "mechanisms go into open_questions, not extra deliveries. Requires "
    "your research state on file first.",
    {
        "handover": {
            "type": "object",
            "properties": {
                "dead_ends": {
                    "type": "array", "items": {"type": "string"},
                    "description": "axis tried, evidence it is spent",
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
                    "description": "your strongest signed warning to a "
                                   "successor with a different lens",
                },
            },
            "required": ["dead_ends", "open_questions", "warning"],
        },
    },
    ["handover"],
)

ABSTAIN_TOOL = _fn(
    "abstain",
    "The empty-seat exit, when your lens provably has no ore here. "
    "Requires your research state on file first; an abstain with no "
    "registered state is a protocol violation.",
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
    "Append one line to your persistent working notes (timestamped, "
    "append-only, survives compaction and resume). This is where "
    "understanding lives between your ears and your context: a map you "
    "just established, a measurement, a decision and its reason — jot it "
    "the moment you know it, then stop re-reading to hold it. Your "
    "research state is the revised model; notes are the cheap running "
    "log. Keep each note one dense line or short block.",
    {"text": {"type": "string"}},
    ["text"],
)


NATIVE_TOOLS: tuple[dict, ...] = (
    # Order is the priority declaration: assistant first, the ledger
    # rhythm second, own eyes after, hands and record last — mirroring
    # the seat-v2 tool block order.
    CONSULT_TOOL,
    WORK_TOOL,
    WAIT_TOOL,
    UPDATE_RESEARCH_STATE_TOOL,
    NOTE_TOOL,
    READ_FILE_TOOL,
    BASH_TOOL,
    WRITE_FILE_TOOL,
    SEARCH_EXPERIMENTS_TOOL,
    INSPECT_EXPERIMENT_TOOL,
    INSPECT_ORIGINATING_TOOL,
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
    {"bash", "read_file", "write_file", "note", "wait"})
NATIVE_FORWARDED_ACTIONS = frozenset({
    "consult", "work", "update_research_state", "search_experiments",
    "inspect_experiment", "inspect_originating_research_state",
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

NATIVE_TOOL_BLOCK = (
    "Your instruments are attached to this conversation as tools, in the "
    "order you should reach for them (your assistant first — it is your "
    "default limb; your own eyes and hands after; the record when you "
    "need what is already known). Each tool's description says what it "
    "does; the order above is your priority. Call them directly — the "
    "platform carries your calls; no envelope, no prose protocol."
)

NATIVE_PROTOCOL_BLOCK = """Working protocol:
- You may call several tools in one turn; they run in order and each
  answer returns to you. Plain text alongside a call is your own
  trajectory note — not a substitute for acting.
- work() returns immediately with a receipt: your assistant takes the
  brief and works on its own. Keep reading, thinking, dispatching — its
  result arrives later as its own message in the conversation, labelled
  with the call id. A job that has not reported back is still running.
  When the next move depends on a running job and nothing else is worth
  doing, wait for it (wait) — that is a legitimate move, not idling.
- A ResearchState is your lease's ONE evolving understanding. Revisit it
  with update_research_state after every work cycle and whenever your
  model materially changes; it must carry ≥1 revision before any exit —
  an exit with nothing on file is a protocol violation.
- Terminal tools (exactly one concludes the lease): deliver_world
  delivers the one world your seat built and self-verified — the
  delivered world is your laboratory's current state, snapshotted
  mechanically by the harness, and its handover (≤400 words) is the ONLY
  thing a successor receives. abstain is the empty-seat exit, when your
  lens provably has no ore here.
- If the budget runs out before you conclude, the harness concludes the
  lease as cut_off — what is on file survives, but the conclusion is not
  yours. Concluding well is part of the work.
"""

_BOUNDARIES_TEMPLATE = """Runtime boundaries:
- You are INSIDE this world: {work} is your writable laboratory (the
  accepted source tree's editable paths, read-write; disposable — nothing
  you write here becomes an artifact; everything else in the tree is
  visible read-only). {scratch} is temporary writable space. {repo} is the
  read-only Git repository: `git show/diff/log` over any prior
  experiment's source; you cannot commit — creating artifacts is the
  executor's job.
- bash, read_file, and write_file see one filesystem — yours. Read
  before you shell: navigate with read_file first; the shell is for what
  reading cannot do (compile, run, measure, git).
- Anything you measure in your lab is for YOUR understanding —
  navigation, not verdicts. Whether a change is faster or correct is the
  Harness's verdict, not yours. Use the lab to understand and to design
  better questions.
- You cannot call the executor or Harness, edit candidates, choose a
  parent, or declare evaluation and Gate facts. Only Harness records are
  authoritative.
""".strip()


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
    """The assistant turn in OpenAI wire format (for the messages list)."""
    return {
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


def wire_tool_result(tool_call_id: str, observation: dict) -> dict:
    """One tool observation in OpenAI wire format."""
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(observation, ensure_ascii=False),
    }
