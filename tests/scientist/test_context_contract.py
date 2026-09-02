from __future__ import annotations

import json

from scientist.agent import (
    _COLD_START, _KILL_KNOCK, build_system_prompt,
)
from scientist.native_tools import (
    NATIVE_PROTOCOL_BLOCK, NATIVE_RUNTIME_BLOCK, NATIVE_TOOLS,
)
from scientist.research_skills import load_research_skill, render_startup_skills


def _system() -> str:
    return build_system_prompt(
        {
            "goal": "understand and improve the system",
            "editable_paths": ["src"],
            "gate_block": "correctness gates must pass",
        },
        roots={
            "work": "/work",
            "repo": "/repo",
            "scratch": "/scratch",
        },
    )


def test_system_context_has_stable_pi_team_order():
    text = _system()
    headings = [
        "# Scientist Charter",
        "# Research Team",
        "# Research Memory",
        "# Research Goal and Hard Constraints",
        "# Current World",
        "# Working with the Team",
        "# World Contact and Evaluation",
        "# Research Records",
        "# Concluding the Research Run",
        "# Runtime Mechanics",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_team_exists_before_working_with_the_team():
    text = _system()
    team = text.index("# Research Team")
    runtime = text.index("# Working with the Team")
    for role in ("Searcher", "Proposer", "Executor", "Challenger"):
        assert role in text[team:runtime]


def test_system_contains_no_fallible_judgment_or_legacy_tool_identity():
    text = _system().lower()
    forbidden = (
        "# current research judgment",
        "canonical cognition",
        "your assistant",
        "your hands",
        "your amplifier",
        "strongest coding agent",
        "default limb",
        "seat of node",
        "your lens is your identity",
        "ask_searcher",
        "assign_executor",
        # old child/seat ontology fossils — they redefine who the
        # scientist is and license solo lab work
        "your laboratory",
        "your seat",
        "concludes the lease",
        "navigation, not verdicts",
        "creating artifacts is the executor's job",
        "you cannot call the executor",
        "deliver your world",
    )
    assert [phrase for phrase in forbidden if phrase in text] == []


def test_every_reachable_surface_obeys_the_same_object_model():
    text = "\n".join([
        _system(),
        json.dumps(NATIVE_TOOLS, ensure_ascii=False),
        NATIVE_RUNTIME_BLOCK,
        NATIVE_PROTOCOL_BLOCK,
        _COLD_START,
        _KILL_KNOCK,
        render_startup_skills(),
        load_research_skill("delegation"),
        load_research_skill("question-reframing"),
    ]).lower()
    forbidden = (
        "your assistant",
        "your hands",
        "your amplifier",
        "default limb",
        "strongest executor",
        "strongest coding agent",
        "assistant finished",
        "after every work cycle",
        "form an initial model before",
        "your lens is your identity",
        "ask_proposer",
        "assign_executor",
        "requires your research state on file first",
        # old child/seat ontology fossils across every reachable surface
        "your laboratory",
        "your seat",
        "concludes the lease",
        "navigation, not verdicts",
        "creating artifacts is the executor's job",
        "you cannot call the executor",
        "deliver your world",
    )
    assert [phrase for phrase in forbidden if phrase in text] == []


# --- interview probe: judgment placement variants (functional) -------------

def _probe():
    import importlib.util
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2]
            / "scripts" / "probe_oneworld.py")
    spec = importlib.util.spec_from_file_location("probe_oneworld", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_judgment_placement_variants():
    from scientist.agent import _JUDGMENT_MARKER, build_system_prompt

    probe = _probe()
    system = build_system_prompt(
        {"goal": "g", "editable_paths": ["src"]})
    assert _JUDGMENT_MARKER not in system
    assert probe._JUDGMENT_BODY not in system

    absent = probe._plateau_messages(probe._REPORT_A, with_judgment=False)
    ordinary = probe._plateau_messages(probe._REPORT_A)
    assert not any(_JUDGMENT_MARKER in str(m.get("content")) for m in absent)
    assert len([m for m in ordinary
                if _JUDGMENT_MARKER in str(m.get("content"))]) == 1
    # absent means the judgment never existed: no revise call either;
    # ordinary keeps the revise turn in history
    assert not any(m.get("role") == "assistant"
                   and "revise_research_judgment" in json.dumps(m)
                   for m in absent)
    assert any(m.get("role") == "assistant"
               and "revise_research_judgment" in json.dumps(m)
               for m in ordinary)

    anchoring = probe._anchoring_system(system)
    assert probe._JUDGMENT_BODY in anchoring
    assert "# Current Research Judgment" in anchoring
    assert len(anchoring) > len(system)
    # anchoring control exists only in the probe, never in production
    assert probe._JUDGMENT_BODY not in build_system_prompt(
        {"goal": "g", "editable_paths": ["src"]})
