"""Seat-local ResearchState tools (transform_worldview path removed)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from simpleevo.jobs.proposer_worker import _result_to_dict
from simpleevo.host.research_agent import (
    WorkingState, _build_telemetry, _build_trace, _register_evidence,
)
from simpleevo.host.research_tools import ResearchTools
from scientist.research_skills import render_research_skill_catalog
from simpleevo.host.runtime import MountMap
from simpleevo.host.scientist import (
    ProposerError,
    _validate_action_guard,
    parse_response,
)
from simpleevo.research_state import ResearchState


class FakeMemory:
    def inspect_experiment(self, experiment_id: str) -> dict:
        return {
            "experiment_id": experiment_id,
            "source_world": {"node_id": "node-sibling", "sha": "sha-sibling"},
            "intervention": {"proposal_id": "p-sibling", "changed_paths": []},
            "condition": {"recorded_gates": []},
            "observation": {"metrics": {"total_ms": 80.0}},
        }

    def inspect_originating_research_state(self, experiment_id: str) -> dict:
        return {
            "ok": True,
            "kind": "SUBJECTIVE_RESEARCH_MEMO",
            "experiment_id": experiment_id,
            "research_state_id": "rs-sibling-001",
            "source_episode_id": "ep-sibling",
            "source_world": {"node_id": "node-sibling", "sha": "sha-sibling"},
            "working_model": "Sibling interpretation, not a fact.",
            "evidence_refs": [],
            "derived_from_research_state_id": None,
            "transformation_id": None,
        }


def _tools(
    tmp_path: Path,
    *,
    episode_id: str = "ep-1",
    node_id: str = "node-1",
    inherited_research_states: dict[str, str] | None = None,
) -> ResearchTools:
    workspace = tmp_path / "work"
    repo = tmp_path / "repo"
    scratch = tmp_path / "scratch"
    home = tmp_path / "home"
    for path in (workspace, repo, scratch, home):
        path.mkdir(exist_ok=True)
    return ResearchTools(
        runtime=object(),
        workspace=workspace,
        repo=repo,
        history_dir=None,
        scratch=scratch,
        world_mount=MountMap(),
        home=home,
        memory_service=FakeMemory(),
        command_timeout_seconds=10,
        command_output_cap_chars=1000,
        node_id=node_id,
        episode_id=episode_id,
        inherited_research_states=inherited_research_states,
    )


def test_parser_accepts_research_state_actions():
    action = parse_response(
        '{"action":"register_research_state",'
        '"working_model":"Repeated work crosses the FCN boundary."}',
        proposal_slots=3,
    )
    assert action["action"] == "register_research_state"


def test_research_skill_is_discoverable_and_loaded_on_demand(tmp_path):
    catalog = render_research_skill_catalog()
    assert "research-expansion" in catalog

    action = parse_response(
        '{"action":"use_research_skill",'
        '"skill_id":"wall-foundation-attack"}',
        proposal_slots=3,
    )
    result = _tools(tmp_path).execute(
        action,
        deadline=time.monotonic() + 10,
        working_state=WorkingState(),
    )

    assert result["ok"] is True
    assert result["skill_id"] == "wall-foundation-attack"
    assert "mis-remembered constraint" in result["content"]


def test_update_research_state_assigns_head_identity(tmp_path):
    state = WorkingState()
    state.session_evidence.add("__source_examined__")
    result = _tools(tmp_path).execute(
        {
            "action": "update_research_state",
            "working_model": "Repeated work crosses the FCN boundary.",
            "evidence_refs": ["source:src/fcn.cc:FCN"],
            "evidence": [{"claim": "c", "status": "belief"}],
            "experiment_log": [{"intent": "i", "verdict": "worse"}],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["ok"] is True
    # One evolving head row per lease, stable id.
    assert result["research_state_id"] == "rs-ep-1-head"
    assert state.research_states[result["research_state_id"]].node_id == "node-1"
    assert state.research_states[result["research_state_id"]].evidence[0][
        "status"] == "belief"

    # The seat never awards itself verified status.
    denied = _tools(tmp_path).execute(
        {
            "action": "update_research_state",
            "working_model": "m",
            "evidence": [{"claim": "c", "status": "verified"}],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert denied["ok"] is False
    assert "graduation" in denied["error"]


def test_registration_rejects_unseen_evidence_references(tmp_path):
    tools = _tools(tmp_path)
    result = tools.execute(
        {
            "action": "update_research_state",
            "working_model": "A concrete model.",
            "evidence_refs": ["experiment:exp-unseen"],
        },
        deadline=time.monotonic() + 10,
        working_state=WorkingState(),
    )
    assert result["ok"] is False
    assert "unseen evidence reference" in result["error"]


def test_registration_rejects_empty_working_model():
    with pytest.raises(ProposerError, match="working_model must be non-empty"):
        parse_response(
            '{"action":"register_research_state","working_model":"  "}',
            proposal_slots=3,
        )


def _proposal_payload(research_state_id: str, instruction: str) -> dict:
    return {
        "research_state_id": research_state_id,
        "instruction": instruction,
        "expectation": "FCN call-local time and total time both decrease.",
        "research_target": {
            "mode": "new",
            "question": "Does boundary-owned state cause repeated work?",
            "mechanisms": ["state-lifecycle"],
            "code_regions": ["OMILRECV2"],
        },
    }


def test_exit_without_registered_state_is_rejected_by_guard():
    state = WorkingState()
    action = parse_response(json.dumps({
        "action": "deliver_world",
        "handover": {
            "dead_ends": ["axis: spent, measured"],
            "open_questions": ["bucket prefetch"],
            "warning": "do not trust the flat profile",
        },
    }), proposal_slots=1)
    verdict = _validate_action_guard(state, [action], Path("."))
    assert verdict is not None
    assert verdict.startswith("exit_without_registered_state")


def test_exit_with_registered_state_passes_guard(tmp_path):
    state = WorkingState()
    _tools(tmp_path).execute(
        {"action": "update_research_state", "working_model": "model"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    action = parse_response(json.dumps({
        "action": "deliver_world",
        "handover": {
            "dead_ends": ["axis: spent, measured"],
            "open_questions": ["bucket prefetch"],
            "warning": "do not trust the flat profile",
        },
    }), proposal_slots=1)
    assert _validate_action_guard(state, [action], Path(".")) is None


def test_deliver_world_parses_and_enforces_handover_shape():
    good = parse_response(json.dumps({
        "action": "deliver_world",
        "handover": {
            "dead_ends": ["axis: spent, measured"],
            "open_questions": ["bucket prefetch", "unused mechanism X @1.4x self-test"],
            "warning": "the flat profile misleads",
        },
    }), proposal_slots=1)
    assert good["action"] == "deliver_world"
    assert good["handover"]["warning"] == "the flat profile misleads"

    # Missing block -> protocol error.
    with pytest.raises(ProposerError, match="open_questions"):
        parse_response(json.dumps({
            "action": "deliver_world",
            "handover": {"dead_ends": ["a"], "warning": "w"},
        }), proposal_slots=1)

    # Over the hard cap -> mechanical rejection (rewrite or degrade).
    padding = " ".join(f"w{i}" for i in range(700))
    with pytest.raises(ProposerError, match="hard cap"):
        parse_response(json.dumps({
            "action": "deliver_world",
            "handover": {
                "dead_ends": [padding],
                "open_questions": ["q"],
                "warning": "w",
            },
        }), proposal_slots=1)

    # Degraded delivery (after two rewrites) parses with the flag.
    degraded = parse_response(json.dumps({
        "action": "deliver_world",
        "handover_compliant": False,
        "handover": {
            "dead_ends": [padding],
            "open_questions": ["q"],
            "warning": "w",
        },
    }), proposal_slots=1)
    assert degraded["handover_compliant"] is False


def test_worker_result_serializes_conclusion():
    result = type("Result", (), {
        "episode_id": "ep-1",
        "node_id": "node-1",
        "outcome": "concluded",
        "conclusion": {"kind": "deliver", "handover": {
            "dead_ends": ["d"], "open_questions": ["q"], "warning": "w",
        }},
        "abstain_reason": None,
        "deliberation_telemetry": {"tool_calls": 3},
        "trace": {"rounds": []},
    })()
    serialized = _result_to_dict(result, world_sha="a" * 40)
    assert serialized["conclusion"]["kind"] == "deliver"
    assert serialized["conclusion"]["world_sha"] == "a" * 40
    assert serialized["conclusion"]["episode_id"] == "ep-1"
    assert serialized["outcome"] == "concluded"


def test_cognitive_telemetry_and_trace_include_ids_not_working_model(tmp_path):
    state = WorkingState()
    registered = _tools(tmp_path).execute(
        {
            "action": "register_research_state",
            "working_model": "A private, potentially long working model.",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    state.counts["proposed_research_states"] = 1
    telemetry = _build_telemetry(state, steps=2, outcome="submit")
    trace = _build_trace(state, round_id=1, outcome="submit")
    assert telemetry["research_states_registered"] == 1
    assert telemetry["proposed_research_states"] == 1
    assert trace["research_state_ids"] == [registered["research_state_id"]]
    assert "working_model" not in json.dumps(trace)


def test_parser_accepts_experiment_and_memo_inspection_actions():
    experiment = parse_response(
        '{"action":"inspect_experiment","experiment_id":"exp-1"}',
        proposal_slots=1,
    )
    memo = parse_response(
        '{"action":"inspect_originating_research_state",'
        '"experiment_id":"exp-1"}',
        proposal_slots=1,
    )
    assert experiment == {
        "action": "inspect_experiment", "experiment_id": "exp-1",
    }
    assert memo == {
        "action": "inspect_originating_research_state",
        "experiment_id": "exp-1",
    }


def test_research_memo_requires_explicit_experiment_inspection(tmp_path):
    result = _tools(tmp_path).execute(
        {
            "action": "inspect_originating_research_state",
            "experiment_id": "exp-1",
        },
        deadline=time.monotonic() + 10,
        working_state=WorkingState(),
    )
    assert result == {
        "ok": False,
        "error": "inspect experiment before requesting its research memo: exp-1",
    }


def test_explicit_inspection_unlocks_memo_and_citable_evidence(tmp_path):
    state = WorkingState()
    tools = _tools(tmp_path)
    action = {"action": "inspect_experiment", "experiment_id": "exp-1"}
    observation = tools.execute(
        action,
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    _register_evidence(state, action, observation)
    memo = tools.execute(
        {
            "action": "inspect_originating_research_state",
            "experiment_id": "exp-1",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert memo["ok"] is True
    assert memo["result"]["kind"] == "SUBJECTIVE_RESEARCH_MEMO"
    assert state.inspected_experiment_ids == {"exp-1"}
    assert "experiment:exp-1" in state.session_evidence


def test_search_hit_does_not_become_citable_evidence():
    state = WorkingState()
    _register_evidence(
        state,
        {"action": "search_experiments", "query": "lookup"},
        {
            "ok": True,
            "result": {
                "relevant": [{"experiment_id": "exp-1"}],
                "contrasting": [],
                "diverse": [],
            },
        },
    )
    assert state.session_evidence == set()
    assert state.inspected_experiment_ids == set()


def test_research_state_can_cite_two_inspected_experiments(tmp_path):
    state = WorkingState()
    tools = _tools(tmp_path)
    for experiment_id in ("exp-a", "exp-b"):
        action = {
            "action": "inspect_experiment", "experiment_id": experiment_id,
        }
        observation = tools.execute(
            action,
            deadline=time.monotonic() + 10,
            working_state=state,
        )
        _register_evidence(state, action, observation)
    registered = tools.execute(
        {
            "action": "register_research_state",
            "working_model": "A and B may be compatible but need one experiment.",
            "evidence_refs": ["experiment:exp-a", "experiment:exp-b"],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert registered["ok"] is True
    record = state.research_states[registered["research_state_id"]]
    assert record.evidence_refs == ("experiment:exp-a", "experiment:exp-b")


def test_abstain_requires_axes_and_registered_state(tmp_path):
    # Shape: axes_checked is required.
    with pytest.raises(ProposerError, match="axes_checked"):
        parse_response(json.dumps({
            "action": "abstain", "reason": "no ore",
        }), proposal_slots=1)

    # Guard: no state on file -> the generalized exit guard fires.
    state = WorkingState()
    action = parse_response(json.dumps({
        "action": "abstain", "reason": "no ore",
        "axes_checked": ["cache axis: all layouts measured slower"],
    }), proposal_slots=1)
    verdict = _validate_action_guard(state, [action], Path("."))
    assert verdict is not None
    assert verdict.startswith("exit_without_registered_state")

    # With a registered state the exit passes.
    _tools(tmp_path).execute(
        {"action": "update_research_state", "working_model": "empty memo"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert _validate_action_guard(state, [action], Path(".")) is None


def test_transform_worldview_is_gone():
    """The advice-level lens infrastructure is removed; a lens is seat
    identity in the system prompt, not a tool."""
    with pytest.raises(ProposerError, match="unknown action"):
        parse_response(
            '{"action":"transform_worldview","operator_id":"G1"}',
            proposal_slots=1,
        )


def test_shared_skills_install_into_claude_config(tmp_path):
    """Seats discover the library as personal skills; program
    governance (audience: scientist) stays with the Scientist."""
    from scientist.research_skills import _SKILLS, install_shared_skills
    installed = install_shared_skills(tmp_path)
    shared = {s.name for s in _SKILLS if s.audience == "shared"}
    assert set(installed) == shared
    assert (tmp_path / "skills" / "wall-foundation-attack" / "SKILL.md").exists()
    assert (tmp_path / "skills" / "representation-shift" / "SKILL.md").exists()
    assert not (tmp_path / "skills" / "mission-identify").exists()
