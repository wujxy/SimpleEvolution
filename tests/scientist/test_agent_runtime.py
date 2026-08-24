from pathlib import Path

from scientist.agent_runtime import AgentRuntime
from scientist.research_agent import WorkingState


class FakeAgent:
    timeout_seconds = 30
    max_steps = 3

    def __init__(self):
        self.replies = [
            ([{"action": "inspect"}], "tool reply"),
            ([{"action": "finish", "value": 7}], "terminal reply"),
        ]

    def _step(self, *args, **kwargs):
        return self.replies.pop(0)


class FakeTools:
    def execute(self, action, *, deadline, working_state):
        return {"ok": True, "value": action["action"]}


class FakeSession:
    def __init__(self):
        self.messages = []

    def append_message(self, role, content, *, round_id):
        self.messages.append((role, content, round_id))


def test_runtime_dispatches_tools_then_returns_role_terminal():
    session = FakeSession()
    checkpoints = []
    runtime = AgentRuntime(FakeAgent())

    result = runtime.run(
        system_prompt="system",
        messages=[],
        session=session,
        current_round=2,
        steps_budget=3,
        source_root=Path("."),
        build_tools=lambda scratch, home: FakeTools(),
        terminal_name="finish",
        budget_nudge="finish soon",
        handle_terminal=lambda action, state, usages, step, outcome: {
            "action": action,
            "outcome": outcome,
        },
        compact=lambda messages, usages, state: None,
        checkpoint=lambda *args, **kwargs: checkpoints.append(True),
    )

    assert result["action"]["value"] == 7
    assert result["outcome"] == "submit"
    assert [item[0] for item in session.messages] == [
        "assistant", "user", "assistant",
    ]
    assert checkpoints == [True]


def test_runtime_returns_abstention_when_budget_expires():
    agent = FakeAgent()
    agent.replies = [([{"action": "inspect"}], "tool reply")]
    runtime = AgentRuntime(agent)

    result = runtime.run(
        system_prompt="system",
        messages=[],
        session=FakeSession(),
        current_round=0,
        steps_budget=1,
        source_root=Path("."),
        build_tools=lambda scratch, home: FakeTools(),
        terminal_name="finish",
        budget_nudge="finish soon",
        handle_terminal=lambda action, state, usages, step, outcome: outcome,
        compact=lambda messages, usages, state: None,
        checkpoint=lambda *args, **kwargs: None,
    )

    assert result == "abstain"
