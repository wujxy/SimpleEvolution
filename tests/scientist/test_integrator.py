import json

import pytest

from scientist.host.integrator import IntegratorAgent, IntegratorError
from scientist.model import ModelReply


class FakeModel:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.reply.pop(0) if isinstance(self.reply, list) else self.reply
        return ModelReply(json.dumps(reply), {"completion_tokens": 2})


def _request():
    return {
        "integration_request_id": "req-1",
        "epoch_id": "epoch-0",
        "target_node_id": "target",
        "donor_experiment_ids": ["exp-a", "exp-b"],
        "selection_rationale": "mature complementary branches",
    }


def test_integrator_returns_one_synthesis_with_request_donors(tmp_path):
    agent = IntegratorAgent(
        model=FakeModel({
            "action": "submit_synthesis",
            "instruction": "Port A and B onto the target without other changes.",
            "working_model": "The mechanisms touch independent regions.",
            "rationale": {"compatibility": "independent"},
            "evidence_refs": ["experiment:exp-a", "experiment:exp-b"],
            "donor_experiment_ids": ["exp-a", "exp-b"],
        }),
        timeout_seconds=30,
        max_steps=2,
    )

    result = agent.integrate(_request(), public_evidence={}, session_dir=tmp_path)

    assert result.outcome == "submitted"
    assert result.donor_experiment_ids == ("exp-a", "exp-b")
    assert (tmp_path / "session.jsonl").exists()


def test_integrator_rejects_donor_outside_request():
    agent = IntegratorAgent(
        model=FakeModel({
            "action": "submit_synthesis",
            "instruction": "Use an unrequested branch.",
            "working_model": "unknown",
            "rationale": {},
            "evidence_refs": [],
            "donor_experiment_ids": ["exp-z"],
        }),
        timeout_seconds=30,
        max_steps=1,
    )

    with pytest.raises(IntegratorError, match="protocol failed"):
        agent.integrate(_request(), public_evidence={})


def test_integrator_can_abstain_on_incompatibility():
    agent = IntegratorAgent(
        model=FakeModel({
            "action": "abstain",
            "reason": "Both donors rewrite the same invariant incompatibly.",
        }),
        timeout_seconds=30,
        max_steps=1,
    )

    result = agent.integrate(_request(), public_evidence={})

    assert result.outcome == "abstained"
    assert "invariant" in result.reason


def test_integrator_retry_resumes_same_request_session(tmp_path):
    reply = {
        "action": "abstain", "reason": "not enough compatible evidence",
    }
    IntegratorAgent(
        model=FakeModel(reply), timeout_seconds=30, max_steps=1,
    ).integrate(_request(), public_evidence={}, session_dir=tmp_path)
    retry_model = FakeModel(reply)

    IntegratorAgent(
        model=retry_model, timeout_seconds=30, max_steps=1,
    ).integrate(_request(), public_evidence={}, session_dir=tmp_path)

    messages = retry_model.calls[0]["messages"]
    assert any(item["role"] == "assistant" for item in messages)
    assert any(
        item["role"] == "user" and "Resume the same request" in item["content"]
        for item in messages
    )


def test_integrator_can_read_target_but_has_no_write_tool(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    workspace.joinpath("kernel.cc").write_text("int kernel() { return 1; }\n")
    model = FakeModel([
        {"action": "read_file", "path": "/work/kernel.cc"},
        {"action": "abstain", "reason": "the requested donors conflict"},
    ])

    result = IntegratorAgent(
        model=model, timeout_seconds=30, max_steps=2,
    ).integrate(
        _request(), public_evidence={}, workspace=workspace, repo=workspace,
    )

    assert result.outcome == "abstained"
    assert any("int kernel" in item["content"] for item in model.calls[1]["messages"])
