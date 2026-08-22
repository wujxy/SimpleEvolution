import json

import pytest

from proposer.integrator import IntegratorAgent, IntegratorError
from proposer.model import ModelReply


class FakeModel:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, **kwargs):
        return ModelReply(json.dumps(self.reply), {"completion_tokens": 2})


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
