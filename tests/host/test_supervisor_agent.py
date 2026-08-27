"""Persistent growth-gate SupervisorAgent: tools, terminals, notebook."""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from scientist.model import ModelReply
from supervisor.agent import (
    SUPERVISOR_PROMPT_VERSION,
    SupervisorAgent,
    SupervisorError,
    load_supervisor_session,
)


class FakeModel:
    """Scripted replies; the last one repeats if the loop asks for more."""

    def __init__(self, replies: list[dict | str]):
        self.replies = deque(replies)

    def complete(self, **kwargs):
        item = self.replies.popleft() if self.replies else {
            "action": "list_nodes",
        }
        text = item if isinstance(item, str) else json.dumps(item)
        return ModelReply(text, {"completion_tokens": 3})


def _batch(cursor_from: int = 0, cursor_to: int = 1) -> dict:
    return {
        "event_batch": {
            "cursor_from": cursor_from,
            "cursor_to": cursor_to,
            "events": [{
                "event_id": cursor_to,
                "type": "root_ready",
                "payload": {"root_node_id": "root"},
            }],
        },
        "epoch": {"epoch_id": "epoch-0", "root_node_id": "root"},
    }


def _tools():
    class _Static:
        def execute(self, action, **_):
            return {"ok": True, "nodes": [
                {"node_id": "root", "allocatable": True}]}

    return _Static()


def test_growth_turn_investigates_then_decides(tmp_path: Path):
    session = load_supervisor_session(tmp_path)
    agent = SupervisorAgent(
        model=FakeModel([
            {"action": "list_nodes"},
            {"action": "submit_growth_decision",
             "seat_purchases": [{"node_id": "root", "lens": "G5"}],
             "rationale": "root deserves growth through inversion; not "
             "buying G10 — the price-list script is the default bet "
             "anyway."},
        ]),
        timeout_seconds=30,
        max_steps=6,
    )

    result = agent.resume(
        session=session, tools=_tools(), batch=_batch(),
        run_context={"goal": "make it fast"},
    )

    assert result.decision_kind == "growth"
    assert result.seat_purchases == (("root", "G5"),)
    assert result.rationale.startswith("root deserves")
    assert session.meta["supervisor_turn"] == 1
    assert (tmp_path / "supervisor" / "session" / "session.jsonl").exists()


def test_notebook_checkpoint_persists_across_turns(tmp_path: Path):
    session = load_supervisor_session(tmp_path)
    first = SupervisorAgent(
        model=FakeModel([
            {"action": "submit_growth_decision", "seat_purchases": [],
             "rationale": "wait."},
            {"notebook": "Root is the only lineage; no evidence yet."},
        ]),
        timeout_seconds=30,
        max_steps=4,
    )
    first.resume(session=session, tools=_tools(), batch=_batch())

    assert "Root is the only lineage" in (
        tmp_path / "supervisor" / "session" / "notebook.md").read_text()

    second = SupervisorAgent(
        model=FakeModel([
            {"action": "submit_growth_decision", "seat_purchases": [],
             "rationale": "still waiting."},
        ]),
        timeout_seconds=30,
        max_steps=4,
    )
    second.resume(session=session, tools=_tools(), batch=_batch(1, 2))

    assert session.meta["supervisor_turn"] == 2
    archive = (
        tmp_path / "supervisor" / "session" / "session.jsonl").read_text()
    # Both wake-up batches are archived (quotes are escaped inside the
    # archived JSON, so count the bare type name).
    assert archive.count("root_ready") == 2
    assert session.meta["prompt_version"] == SUPERVISOR_PROMPT_VERSION


def test_growth_output_rejects_extra_fields(tmp_path: Path):
    # The model insists on the extra field through every protocol repair,
    # so the rejection (not a step-budget stop) is what surfaces.
    invalid = {
        "action": "submit_growth_decision",
        "seat_purchases": [{"node_id": "root", "lens": "G5"}],
        "rationale": "ok",
        "proposal_slots": 2,
    }
    agent = SupervisorAgent(
        model=FakeModel([invalid] * 6),
        timeout_seconds=30,
        max_steps=8,
    )
    with pytest.raises(SupervisorError, match="action protocol failed"):
        agent.resume(
            session=load_supervisor_session(tmp_path), tools=_tools(),
            batch=_batch(),
        )


def test_integration_output_rejects_mechanical_ids(tmp_path: Path):
    invalid = {
        "action": "submit_integration_request",
        "integration_request_id": "req-1",
        "target_node_id": "root",
        "donor_experiment_ids": ["exp-1"],
        "selection_rationale": "matured",
    }
    agent = SupervisorAgent(
        model=FakeModel([invalid] * 6),
        timeout_seconds=30,
        max_steps=8,
    )
    with pytest.raises(SupervisorError, match="integration_request_id"):
        agent.resume(
            session=load_supervisor_session(tmp_path), tools=_tools(),
            batch=_batch(),
        )


def test_integration_and_epoch_review_terminals(tmp_path: Path):
    integrator = SupervisorAgent(
        model=FakeModel([{
            "action": "submit_integration_request",
            "target_node_id": "root",
            "donor_experiment_ids": ["exp-1"],
            "selection_rationale": "branches matured",
        }]),
        timeout_seconds=30, max_steps=2,
    )
    result = integrator.resume(
        session=load_supervisor_session(tmp_path / "a"),
        tools=_tools(), batch=_batch(),
    )
    assert result.decision_kind == "integration_request"
    assert result.detail["target_node_id"] == "root"
    # The request id is harness state, never part of the model's output.
    assert "integration_request_id" not in result.detail

    reviewer = SupervisorAgent(
        model=FakeModel([{
            "action": "submit_epoch_review",
            "integration_request_id": "req-1",
            "review": "promote",
            "rationale": "candidate covers donors",
        }]),
        timeout_seconds=30, max_steps=2,
    )
    review = reviewer.resume(
        session=load_supervisor_session(tmp_path / "b"),
        tools=_tools(), batch=_batch(),
    )
    assert review.decision_kind == "epoch_review"
    assert review.detail["review"] == "promote"


def test_empty_selection_is_a_valid_wait(tmp_path: Path):
    agent = SupervisorAgent(
        model=FakeModel([{
            "action": "submit_growth_decision", "seat_purchases": [],
            "rationale": "siblings in flight; wait.",
        }]),
        timeout_seconds=30, max_steps=2,
    )
    result = agent.resume(
        session=load_supervisor_session(tmp_path), tools=_tools(),
        batch=_batch(),
    )
    assert result.decision_kind == "growth"
    assert result.seat_purchases == ()


def test_step_budget_exhaustion_is_an_error_not_a_default(tmp_path: Path):
    agent = SupervisorAgent(
        model=FakeModel([{"action": "list_nodes"}]),
        timeout_seconds=30,
        max_steps=2,
    )
    with pytest.raises(SupervisorError, match="step budget"):
        agent.resume(
            session=load_supervisor_session(tmp_path), tools=_tools(),
            batch=_batch(),
        )


def test_live_context_compaction_keeps_archive_complete(tmp_path: Path):
    from simpleevo.host.scientist import ContextPolicy

    session = load_supervisor_session(tmp_path)
    agent = SupervisorAgent(
        model=FakeModel([
            {"action": "list_nodes"},
            {"action": "submit_growth_decision", "seat_purchases": [],
             "rationale": "wait."},
            {"notebook": "compacted turn survives in the archive."},
        ]),
        timeout_seconds=30,
        max_steps=8,
        # A threshold of one token forces compaction after every step.
        context_policy=ContextPolicy(
            emergency_threshold_tokens=1, window_pairs=2,
            window_max_chars=2000),
    )

    result = agent.resume(
        session=session, tools=_tools(), batch=_batch())

    assert result.decision_kind == "growth"
    archive = (
        tmp_path / "supervisor" / "session" / "session.jsonl").read_text()
    # The append-only archive still holds every turn, including the tool
    # observation that compaction shed from the live context.
    lines = [json.loads(line) for line in archive.splitlines()]
    assert any("tool_results" in item["content"] for item in lines)
    assert any(item["role"] == "assistant" for item in lines)
    assert (tmp_path / "supervisor" / "session" / "notebook.md").read_text()\
        .startswith("compacted turn survives")
