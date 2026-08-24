"""Seat-local ResearchState tools (transform_worldview path removed)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scientist.cli import _enrich_proposals, _proposal_to_dict, _result_to_dict
from scientist.research_agent import (
    WorkingState, _build_telemetry, _build_trace, _register_evidence,
)
from scientist.research_tools import ResearchTools
from scientist.research_skills import render_research_skill_catalog
from scientist.runtime import MountMap
from scientist.scientist import (
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
    assert "reframe_inherited_problem" in catalog

    action = parse_response(
        '{"action":"use_research_skill",'
        '"skill_id":"reframe_inherited_problem"}',
        proposal_slots=3,
    )
    result = _tools(tmp_path).execute(
        action,
        deadline=time.monotonic() + 10,
        working_state=WorkingState(),
    )

    assert result["ok"] is True
    assert result["skill_id"] == "reframe_inherited_problem"
    assert "The predecessor material is a memo" in result["content"]


def test_register_research_state_assigns_host_identity(tmp_path):
    state = WorkingState()
    state.session_evidence.add("__source_examined__")
    result = _tools(tmp_path).execute(
        {
            "action": "register_research_state",
            "working_model": "Repeated work crosses the FCN boundary.",
            "evidence_refs": ["source:src/fcn.cc:FCN"],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["ok"] is True
    assert result["research_state_id"] == "rs-ep-1-001"
    assert state.research_states[result["research_state_id"]].node_id == "node-1"


def test_registration_rejects_unknown_local_references(tmp_path):
    tools = _tools(tmp_path)
    for field, value in (
        ("derived_from_research_state_id", "rs-missing"),
    ):
        result = tools.execute(
            {
                "action": "register_research_state",
                "working_model": "A concrete model.",
                field: value,
            },
            deadline=time.monotonic() + 10,
            working_state=WorkingState(),
        )
        assert result["ok"] is False
        assert value in result["error"]


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


def test_submit_proposal_requires_registered_research_state():
    state = WorkingState()
    action = parse_response(json.dumps({
        "action": "submit_proposals",
        "proposals": [_proposal_payload(
            "rs-ep-1-999",
            "Preserve event-level invariants across FCN calls.",
        )],
    }), proposal_slots=3)
    assert (
        _validate_action_guard(state, [action], Path("."))
        == "unknown_research_state"
    )


def test_one_registered_state_can_submit_two_proposals(tmp_path):
    state = WorkingState()
    registered = _tools(tmp_path).execute(
        {
            "action": "register_research_state",
            "working_model": "Repeated work crosses the FCN boundary.",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    state_id = registered["research_state_id"]
    first = _proposal_payload(state_id, "Move ownership to the event boundary.")
    second = _proposal_payload(state_id, "Cache the invariant at the call boundary.")
    second["material_difference"] = "Tests caching rather than ownership."
    action = parse_response(json.dumps({
        "action": "submit_proposals",
        "proposals": [first, second],
    }), proposal_slots=3)
    assert _validate_action_guard(state, [action], Path(".")) is None
    assert [item.research_state_id for item in action["proposals"]] == [
        state_id,
        state_id,
    ]


def test_explore_and_synthesis_terminal_actions_are_distinct(tmp_path):
    state = WorkingState()
    state_id = _tools(tmp_path).execute(
        {"action": "register_research_state", "working_model": "model"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )["research_state_id"]
    explore = parse_response(json.dumps({
        "action": "submit_explorations",
        "proposals": [_proposal_payload(state_id, "Try mechanism A.")],
    }), proposal_slots=2)
    assert explore["operation"] == "explore"
    assert explore["action"] == "submit_proposals"

    synthesis = parse_response(json.dumps({
        "action": "submit_synthesis",
        "proposal": _proposal_payload(state_id, "Port donor result."),
        "donor_experiment_ids": ["exp-1"],
    }), proposal_slots=2)
    assert synthesis["operation"] == "synthesize"
    assert synthesis["donor_experiment_ids"] == ("exp-1",)
    assert _validate_action_guard(state, [synthesis], Path(".")) == "uninspected_donor"


def test_synthesis_rejects_multiple_proposals():
    with pytest.raises(ProposerError, match="proposal"):
        parse_response(json.dumps({
            "action": "submit_synthesis",
            "proposals": [_proposal_payload("rs-1", "A")],
            "donor_experiment_ids": ["exp-1"],
        }), proposal_slots=2)


def test_synthesis_metadata_reaches_proposal_artifact():
    proposal = parse_response(json.dumps({
        "action": "submit_synthesis",
        "proposal": _proposal_payload("rs-1", "Port donor result."),
        "donor_experiment_ids": ["exp-1"],
    }), proposal_slots=1)["proposals"][0]

    artifact = _enrich_proposals(
        (proposal,), ["prop-1"],
        research_operation="synthesize",
        donor_experiment_ids=("exp-1",),
    )[0]

    assert artifact["research_operation"] == "synthesize"
    assert artifact["donor_experiment_ids"] == ["exp-1"]


def test_worker_result_serializes_research_records_and_proposal_linkage():
    research_state = ResearchState(
        research_state_id="rs-ep-1-001",
        node_id="node-1",
        episode_id="ep-1",
        derived_from_research_state_id=None,
        transformation_id="ct-ep-1-001",
        working_model="Repeated work crosses the FCN boundary.",
        evidence_refs=("source:src/fcn.cc:FCN",),
        created_at=1.0,
    )
    proposal = parse_response(json.dumps({
        "action": "submit_proposals",
        "proposals": [_proposal_payload(
            research_state.research_state_id,
            "Move ownership to the event boundary.",
        )],
    }), proposal_slots=1)["proposals"][0]
    result = type("Result", (), {
        "episode_id": "ep-1",
        "node_id": "node-1",
        "outcome": "submit",
        "abstain_reason": None,
        "deliberation_telemetry": {},
        "trace": {},
        "research_states": (research_state,),
    })()
    serialized = _result_to_dict(
        result, [_proposal_to_dict(proposal, "prop-1")],
    )
    assert serialized["research_states"][0]["research_state_id"] == "rs-ep-1-001"
    assert serialized["proposals"][0]["research_state_id"] == "rs-ep-1-001"
    assert serialized["proposals"][0]["rationale"]["expectation"] == (
        "FCN call-local time and total time both decrease."
    )


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


def test_abstain_without_registered_state_is_rejected():
    """Empty-seat exit contract: an abstain must leave its memo behind."""
    action = parse_response(
        '{"action":"abstain","reason":"Nothing to ask here."}',
        proposal_slots=1,
    )
    assert _validate_action_guard(
        WorkingState(), [action], Path("."),
    ) == (
        "empty_seat_memo_missing: register your research "
        "state first (what you checked along your lens's "
        "axes and why they are all empty), then abstain — "
        "an empty exit without a memo erases your seat's "
        "investigation"
    )


def test_abstain_with_registered_state_passes(tmp_path):
    state = WorkingState()
    _tools(tmp_path).execute(
        {"action": "register_research_state", "working_model": "Lens axes "
         "audited; all empty on this world."},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    action = parse_response(
        '{"action":"abstain","reason":"All lens axes empty."}',
        proposal_slots=1,
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
