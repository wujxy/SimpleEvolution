"""Long-horizon persistence invariants.

The 100-hour contract: a crash leaves a conclusion (never nothing), the
wire log replays the conversation exactly (including fields the readable
archive drops), and resume is a rebuild from that log — not a restart.
"""
from __future__ import annotations

import json

from scientist.native_tools import (
    CHALLENGER_TOOL, PROPOSER_TOOL, SEARCHER_TOOL,
)
from scientist.scientist_session import ScientistSession


def _session(tmp_path) -> ScientistSession:
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    return ScientistSession.load_or_create(
        session_dir, prompt_version="test-version", episode_id="test-ep",
    )


def test_wire_log_roundtrip_preserves_every_field(tmp_path):
    """The wire log is the single source of truth: tool_calls, the
    reasoning_content a thinking-mode provider requires on replay, and
    tool results survive a write→load cycle byte-for-byte."""
    session = _session(tmp_path)
    turns = [
        {"role": "user", "content": "begin"},
        {"role": "assistant", "content": "narration",
         "reasoning_content": "hidden thinking, must survive",
         "tool_calls": [{"id": "c1", "type": "function", "function": {
             "name": "executor", "arguments": "{\"brief\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "c1",
         "content": "{\"ok\": true}"},
        {"role": "user", "content": "[report]"},
    ]
    for turn in turns:
        session.append_wire(turn)
    assert session.load_wire_messages() == turns


def test_wire_records_carry_ts_the_replay_view_strips(tmp_path):
    """Every wire record is stamped with a wall-clock epoch — the only
    clock in the whole stream, without which tool durations and idle
    gaps are unrecoverable after the fact. The stamp lives on the disk
    record for analysis; the replayed conversation never sees it (the
    endpoint must not receive fields it never produced)."""
    session = _session(tmp_path)
    session.append_wire({"role": "user", "content": "tick"})
    rows = [json.loads(line) for line
            in session.wire_path.read_text().splitlines()]
    assert isinstance(rows[0]["ts"], float)
    assert session.load_wire_messages() == [{"role": "user",
                                             "content": "tick"}]


def test_wire_log_tolerates_torn_trailing_line(tmp_path):
    """A crash mid-write leaves at most one torn line; load skips it."""
    session = _session(tmp_path)
    session.append_wire({"role": "user", "content": "kept"})
    with session.wire_path.open("a", encoding="utf-8") as fh:
        fh.write('{"role": "assistant", "content": "tor')  # no newline
    assert session.load_wire_messages() == [
        {"role": "user", "content": "kept"},
    ]


def test_wire_log_completes_dangling_tool_pair(tmp_path):
    """A hard kill between an assistant tool_calls message and its tool
    result leaves the pair open forever — later appends (resume notices,
    budget notes) land after the gap, and replayed as-is the model
    endpoint rejects the whole conversation. Load completes the view
    with an interrupted marker; the file on disk keeps the honest gap."""
    session = _session(tmp_path)
    session.append_wire({"role": "user", "content": "begin"})
    session.append_wire({"role": "assistant", "content": None,
                         "tool_calls": [
                             {"id": "c1", "type": "function", "function": {
                                 "name": "executor", "arguments": "{}"}},
                             {"id": "c2", "type": "function", "function": {
                                 "name": "proposer", "arguments": "{}"}}]})
    session.append_wire({"role": "tool", "tool_call_id": "c2",
                         "content": "{\"ok\": true}"})
    session.append_wire({"role": "user", "content": "[resumed notice]"})
    replayed = session.load_wire_messages()
    assert [m.get("role") for m in replayed] == [
        "user", "assistant", "tool", "tool", "user"]
    assert replayed[3]["tool_call_id"] == "c1"
    assert "interrupted" in replayed[3]["content"]
    on_disk = [json.loads(line) for line in
               session.wire_path.read_text("utf-8").splitlines()]
    assert len(on_disk) == 4
    assert on_disk[-1]["role"] == "user"  # the gap itself is never rewritten


def test_every_seat_advertises_its_own_time_box():
    """The 900s default killed two challengers in one arm: the backend
    accepted timeout_minutes but the schemas never told the model it
    could ask. Every seat must carry the parameter."""
    for tool in (SEARCHER_TOOL, PROPOSER_TOOL, CHALLENGER_TOOL):
        assert "timeout_minutes" in tool["function"]["parameters"][
            "properties"]


def test_cli_writes_a_crashed_conclusion_when_the_loop_dies(
        tmp_path, monkeypatch):
    """The exit contract is an invariant: even an unhandled crash leaves
    conclusion.json (outcome=crashed), so the harness can always tell a
    dead run from a live one and a resume has ground to stand on."""
    import scientist.cli as cli
    from scientist.model import ModelError

    world = tmp_path / "world"
    (world / ".scientist").mkdir(parents=True)
    spec = {
        "goal": "test goal", "gate_block": "gates", "editable_paths": [],
        "episode_id": "crash-ep",
        "model": {"api": "openai", "model": "m", "base_url": "http://x",
                  "api_key": "k"},
        "assistant": {"command": "claude", "env": {}},
        "budget": {"steps": 5, "wall_seconds": 60},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def _boom(*args, **kwargs):
        raise ModelError("provider contract flipped mid-run")

    monkeypatch.setattr(cli, "run_episode", _boom)
    rc = cli.main([
        "--spec", str(spec_path), "--world", str(world),
    ])
    assert rc == 1
    conclusion = json.loads(
        (world / ".scientist" / "conclusion.json").read_text("utf-8"))
    assert conclusion["outcome"] == "crashed"
    assert "provider contract flipped" in conclusion["conclusion"]["reason"]
