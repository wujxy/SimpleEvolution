import json

import pytest

from proposer.model import ModelReply
from proposer.supervisor import (
    AllocationDirective,
    GroupSnapshot,
    SnapshotNode,
    SupervisorAgent,
    SupervisorError,
)


class FakeModel:
    def __init__(self, reply: dict):
        self.reply = reply

    def complete(self, **kwargs):
        return ModelReply(json.dumps(self.reply), {"completion_tokens": 3})


def _snapshot():
    return GroupSnapshot(
        epoch_id="epoch-0",
        epoch_root_node_id="root",
        watermark="watermark-1",
        eligible_nodes=(
            SnapshotNode("root", None, None, "abc", 0, "active", {"score": 1}),
            SnapshotNode("branch", "root", "exp-1", "def", 1, "dormant", {"score": 2}),
        ),
    )


def test_supervisor_agent_uses_shared_runtime_to_return_typed_decision(tmp_path):
    agent = SupervisorAgent(
        model=FakeModel({
            "action": "submit_supervisor_decision",
            "decision_id": "decision-1",
            "epoch_id": "epoch-0",
            "snapshot_watermark": "watermark-1",
            "allocations": [{"node_id": "branch", "proposal_slots": 2}],
            "rationale": "Protect a distinct tested lineage.",
            "evidence_refs": ["experiment:exp-1"],
        }),
        timeout_seconds=30,
        max_steps=2,
    )

    decision = agent.decide(_snapshot(), proposer_capacity=1, session_dir=tmp_path)

    assert decision.allocations == (AllocationDirective("branch", 2),)
    assert decision.snapshot_watermark == "watermark-1"
    assert (tmp_path / "session.jsonl").exists()


def test_supervisor_agent_rejects_technical_proposals():
    agent = SupervisorAgent(
        model=FakeModel({"action": "submit_proposals", "proposals": []}),
        timeout_seconds=30,
        max_steps=1,
    )

    with pytest.raises(SupervisorError, match="protocol failed"):
        agent.decide(_snapshot(), proposer_capacity=1)
