"""Provider-native tool-calling channel: stream assembly and wire helpers.

The fragments below mirror the OpenAI streaming wire format: tool calls
arrive as per-index deltas, the first carrying id + function name, later
ones appending argument bytes.
"""
from types import SimpleNamespace

import pytest

from scientist.model import EmptyReplyError, OpenAICompatChatModel, ToolCall
from scientist.native_tools import (
    NATIVE_TOOLS,
    FORWARDABLE_ACTIONS,
    native_actions,
    wire_assistant_message,
    wire_tool_result,
)


def _chunk(content=None, tool_calls=None, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(finish_reason=finish_reason, delta=delta)],
    )


def _tc(index, tc_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index, id=tc_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._chunks)


class FakeClient:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(chunks))


def _model(chunks):
    client = FakeClient(chunks)
    return OpenAICompatChatModel(client=client, model="test"), client


def test_streamed_tool_calls_assemble_and_parse():
    model, client = _model([
        _chunk(tool_calls=[_tc(0, tc_id="call_1", name="work")]),
        _chunk(tool_calls=[
            _tc(0, arguments='{"instruction": "build the bucketed '),
        ]),
        _chunk(tool_calls=[
            _tc(0, arguments='index","mode": "continue"}'),
        ]),
        _chunk(tool_calls=[
            _tc(1, tc_id="call_2", name="consult",
                arguments='{"question": "why?"}'),
        ]),
        _chunk(finish_reason="tool_calls"),
        _chunk(usage=SimpleNamespace(
            model_dump=lambda: {"completion_tokens": 90}),
        ),
    ])
    reply = model.complete(system="s", messages=[], timeout_seconds=5,
                           tools=list(NATIVE_TOOLS))
    assert [c.name for c in reply.tool_calls] == ["work", "consult"]
    assert reply.tool_calls[0].arguments == {
        "instruction": "build the bucketed index", "mode": "continue",
    }
    assert reply.tool_calls[0].id == "call_1"
    assert reply.usage == {"completion_tokens": 90}
    # Native channel: tools sent, json_object guard NOT sent.
    assert client.chat.completions.kwargs["tools"] == list(NATIVE_TOOLS)
    assert "response_format" not in client.chat.completions.kwargs


def test_unparsable_arguments_kept_raw():
    model, _client = _model([
        _chunk(tool_calls=[
            _tc(0, tc_id="c1", name="bash", arguments="{not json"),
        ]),
    ])
    reply = model.complete(system="s", messages=[], timeout_seconds=5,
                           tools=list(NATIVE_TOOLS))
    assert reply.tool_calls[0].arguments is None
    assert reply.tool_calls[0].arguments_raw == "{not json"


def test_empty_reply_without_tool_calls_raises():
    model, _client = _model([_chunk(finish_reason="length")])
    with pytest.raises(EmptyReplyError):
        model.complete(system="s", messages=[], timeout_seconds=5,
                       tools=list(NATIVE_TOOLS))


def test_empty_text_with_tool_calls_is_a_healthy_reply():
    model, _client = _model([
        _chunk(tool_calls=[
            _tc(0, tc_id="c1", name="abstain",
                arguments='{"reason": "no ore", "axes_checked": []}'),
        ]),
        _chunk(finish_reason="tool_calls"),
    ])
    reply = model.complete(system="s", messages=[], timeout_seconds=5,
                           tools=list(NATIVE_TOOLS))
    assert reply.text == ""
    assert len(reply.tool_calls) == 1


def test_json_protocol_track_unchanged_without_tools():
    model, client = _model([_chunk(content='{"action": "read_file"}')])
    reply = model.complete(system="s", messages=[], timeout_seconds=5)
    assert reply.tool_calls == ()
    assert client.chat.completions.kwargs["response_format"] == {
        "type": "json_object"}
    assert "tools" not in client.chat.completions.kwargs


def test_native_actions_and_wire_helpers():
    reply_text = "planning aloud"
    calls = (
        ToolCall(id="c1", name="consult",
                 arguments={"question": "refute this"}, arguments_raw="{}"),
        ToolCall(id="c2", name="bash", arguments=None,
                 arguments_raw="{broken"),
    )

    class _Reply:
        text = reply_text
        tool_calls = calls

    actions = native_actions(_Reply())
    assert actions[0] == {
        "action": "consult", "tool_call_id": "c1",
        "question": "refute this",
    }
    assert actions[1]["_arguments_raw"] == "{broken"

    wire = wire_assistant_message(_Reply(), actions)
    assert wire["role"] == "assistant"
    assert wire["content"] == reply_text
    assert wire["tool_calls"][1]["function"]["arguments"] == "{broken"

    result = wire_tool_result("c1", {"ok": True})
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "c1"


def test_forwardable_set_excludes_local_and_terminal():
    assert FORWARDABLE_ACTIONS == {
        "consult", "work", "update_research_state", "search_experiments",
        "inspect_experiment", "inspect_originating_research_state",
        "use_research_skill",
    }
    assert "bash" not in FORWARDABLE_ACTIONS
    assert "deliver_world" not in FORWARDABLE_ACTIONS
