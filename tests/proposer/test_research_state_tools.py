"""Scientist-local ResearchState and CognitiveTransformation tools."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from proposer.cognitive_transformer import CognitiveTransformer
from proposer.cli import _proposal_to_dict, _result_to_dict
from proposer.model import ModelReply
from proposer.research_agent import WorkingState, _build_telemetry, _build_trace
from proposer.research_tools import ResearchTools
from proposer.runtime import MountMap
from proposer.scientist import (
    ProposerError,
    _validate_action_guard,
    parse_response,
)
from simpleevo.generator import Generator
from simpleevo.research_state import CognitiveTransformation, ResearchState


class FakeModel:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return ModelReply(self.text, {"completion_tokens": 7})


class FakeMemory:
    pass


def _tools(
    tmp_path: Path,
    *,
    model: FakeModel | None = None,
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
    model = model or FakeModel("Challenge the current boundary.")
    generators = {
        "G1": Generator("G1", "Assumption Attack", "Attack an assumption."),
        "G2": Generator("G2", "Boundary Shift", "Shift the boundary."),
    }
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
        cognitive_transformer=CognitiveTransformer(
            model=model,
            generators=generators,
            episode_seed="The current world has repeated FCN work.",
        ),
        inherited_research_states=inherited_research_states,
    )


def test_parser_accepts_research_state_actions():
    action = parse_response(
        '{"action":"register_research_state",'
        '"working_model":"Repeated work crosses the FCN boundary."}',
        proposal_slots=3,
    )
    assert action["action"] == "register_research_state"


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


def test_transform_worldview_uses_one_generator_and_records_challenge(tmp_path):
    model = FakeModel("Question whether FCN is the natural ownership boundary.")
    state = WorkingState()
    result = _tools(tmp_path, model=model).execute(
        {"action": "transform_worldview", "operator_id": "G2"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["transformation_id"] == "ct-ep-1-001"
    assert state.transformations[result["transformation_id"]].operator_id == "G2"
    assert len(model.calls) == 1
    assert "Do not generate implementation proposals" in model.calls[0]["system"]


def test_transform_worldview_rejects_unknown_generator(tmp_path):
    result = _tools(tmp_path).execute(
        {"action": "transform_worldview", "operator_id": "G99"},
        deadline=time.monotonic() + 10,
        working_state=WorkingState(),
    )
    assert result == {"ok": False, "error": "unknown generator: G99"}


def test_registration_rejects_unknown_local_references(tmp_path):
    tools = _tools(tmp_path)
    for field, value in (
        ("derived_from_research_state_id", "rs-missing"),
        ("transformation_id", "ct-missing"),
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


def test_transform_rejects_state_owned_by_another_episode(tmp_path):
    state = WorkingState()
    state.research_states["rs-other-001"] = ResearchState(
        research_state_id="rs-other-001",
        node_id="node-1",
        episode_id="other-episode",
        derived_from_research_state_id=None,
        transformation_id=None,
        working_model="A state from another episode.",
        evidence_refs=(),
        created_at=1.0,
    )
    result = _tools(tmp_path).execute(
        {
            "action": "transform_worldview",
            "source_research_state_id": "rs-other-001",
            "operator_id": "G1",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["ok"] is False
    assert "another episode" in result["error"]


def test_child_can_transform_and_derive_from_inherited_state(tmp_path):
    parent_id = "rs-parent-001"
    parent_model = "The parent boundary loses reusable state."
    model = FakeModel("Reconsider the lifetime boundary.")
    tools = _tools(
        tmp_path,
        model=model,
        inherited_research_states={parent_id: parent_model},
    )
    state = WorkingState()
    transformed = tools.execute(
        {
            "action": "transform_worldview",
            "source_research_state_id": parent_id,
            "operator_id": "G2",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    registered = tools.execute(
        {
            "action": "register_research_state",
            "working_model": "The Child should own state at event lifetime.",
            "derived_from_research_state_id": parent_id,
            "transformation_id": transformed["transformation_id"],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert registered["ok"] is True
    assert parent_model in model.calls[0]["messages"][0]["content"]


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


def test_worker_result_serializes_cognitive_records_and_proposal_linkage():
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
    transformation = CognitiveTransformation(
        transformation_id="ct-ep-1-001",
        node_id="node-1",
        episode_id="ep-1",
        source_research_state_id=None,
        operator_id="G2",
        challenge="Question the FCN ownership boundary.",
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
        "transformations": (transformation,),
    })()
    serialized = _result_to_dict(
        result, [_proposal_to_dict(proposal, "prop-1")],
    )
    assert serialized["research_states"][0]["research_state_id"] == "rs-ep-1-001"
    assert serialized["transformations"][0]["operator_id"] == "G2"
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
    transformed = _tools(tmp_path).execute(
        {"action": "transform_worldview", "operator_id": "G1"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    state.counts["proposed_research_states"] = 1
    telemetry = _build_telemetry(state, steps=2, outcome="submit")
    trace = _build_trace(state, round_id=1, outcome="submit")
    assert telemetry["research_states_registered"] == 1
    assert telemetry["transformations_requested"] == 1
    assert telemetry["proposed_research_states"] == 1
    assert trace["research_state_ids"] == [registered["research_state_id"]]
    assert trace["transformation_ids"] == [transformed["transformation_id"]]
    assert "working_model" not in json.dumps(trace)
