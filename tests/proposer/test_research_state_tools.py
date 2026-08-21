"""Scientist-local ResearchState and CognitiveTransformation tools."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from proposer.cognitive_transformer import CognitiveTransformer
from proposer.model import ModelReply
from proposer.research_agent import WorkingState
from proposer.research_tools import ResearchTools
from proposer.runtime import MountMap
from proposer.scientist import ProposerError, parse_response
from simpleevo.generator import Generator
from simpleevo.research_state import ResearchState


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
