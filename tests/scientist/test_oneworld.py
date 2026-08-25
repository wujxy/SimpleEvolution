"""Tests for the oneworld package path: stdlib transport, ledger,
exit-contract validation, dual-spelling paths, boundaries rendering."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from scientist.agent import (
    _HANDOVER_HARD_WORD_CAP,
    _HANDOVER_SOFT_WORD_CAP,
    validate_conclusion,
)
from scientist.ledger import LocalLedger
from scientist.model_stdlib import StdlibChatModel, _HttpStatusError
from scientist.native_tools import render_native_boundaries
from scientist.world import LocalWorld


# --- LocalLedger -------------------------------------------------------------


@pytest.fixture()
def ledger(tmp_path: Path) -> LocalLedger:
    return LocalLedger(tmp_path)


def _seed_experiments(ledger: LocalLedger, rows: list[dict]) -> None:
    for row in rows:
        ledger.experiments_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger.experiments_path.open("a", encoding="utf-8") as h:
            h.write(json.dumps(row) + "\n")


def test_state_on_file_requires_a_row(ledger: LocalLedger):
    assert not ledger.state_on_file()
    reply = ledger.update_research_state({
        "working_model": "binary search dominates",
        "evidence_refs": ["src/Simulation.c:120"],
    })
    assert reply["ok"] and reply["revision"] == 1
    assert ledger.state_on_file()
    second = ledger.update_research_state({
        "working_model": "revised", "evidence_refs": [],
    })
    assert second["revision"] == 2
    assert ledger.current_state()["working_model"] == "revised"


def test_update_research_state_validates(ledger: LocalLedger):
    assert not ledger.update_research_state(
        {"working_model": "  "})["ok"]
    assert not ledger.update_research_state(
        {"working_model": "m", "evidence_refs": [1]})["ok"]


def test_search_experiments_buckets(ledger: LocalLedger):
    _seed_experiments(ledger, [
        {"experiment_id": "e1", "parent_node_id": "n0", "parent_sha": "a",
         "status": "OK", "gate_passed": True, "metrics": {"speed": 1},
         "changed_paths": ["src/a.c"], "instruction": "bucket the index"},
        {"experiment_id": "e2", "parent_node_id": "n0", "parent_sha": "a",
         "status": "OK", "gate_passed": False, "metrics": {"speed": 2},
         "changed_paths": ["src/b.c"], "instruction": "bucket via soa"},
    ])
    reply = ledger.search_experiments(
        {"query": "bucket", "filters": {}, "limit": 10, "buckets": True})
    assert reply["ok"]
    assert [r["experiment_id"] for r in reply["relevant"]] == ["e1", "e2"]
    # gate-contrasting bucket surfaces the failed sibling
    assert [r["experiment_id"] for r in reply["contrasting"]] == ["e2"]
    flat = ledger.search_experiments(
        {"query": "", "filters": {}, "limit": 10, "buckets": False})
    assert {r["experiment_id"] for r in flat["results"]} == {"e1", "e2"}
    filtered = ledger.search_experiments(
        {"query": "", "filters": {"gate_passed": True},
         "limit": 10, "buckets": False})
    assert [r["experiment_id"] for r in filtered["results"]] == ["e1"]


def test_inspect_experiment_and_originating(ledger: LocalLedger):
    _seed_experiments(ledger, [{
        "experiment_id": "e1", "parent_node_id": "n0", "parent_sha": "aa",
        "child_node_id": "n1", "child_sha": "bb", "status": "OK",
        "gate_passed": True, "metrics": {"speed": 2},
        "parent_metrics": {"speed": 1}, "changed_paths": ["src/a.c"],
        "instruction": "bucket it",
        "gate_results": {"verify": {"passed": True, "detail": "checksum"}},
        "originating_research_state": {
            "research_state_id": "rs-0001", "working_model": "model",
            "evidence_refs": ["x"],
        },
    }])
    detail = ledger.inspect_experiment({"experiment_id": "e1"})
    assert detail["ok"]
    assert detail["source_world"]["sha"] == "aa"
    assert detail["observation"]["gate"]["passed"] is True
    assert detail["intervention"]["instruction"] == "bucket it"
    memo = ledger.inspect_originating_research_state(
        {"experiment_id": "e1"})
    assert memo["kind"] == "SUBJECTIVE_RESEARCH_MEMO"
    assert memo["working_model"] == "model"
    missing = ledger.inspect_experiment({"experiment_id": "nope"})
    assert not missing["ok"]


# --- the exit contract -------------------------------------------------------


class _FakeLedger:
    def __init__(self, on_file: bool):
        self._on_file = on_file

    def state_on_file(self) -> bool:
        return self._on_file


def _handover(words: int) -> dict:
    filler = " ".join(["w"] * words)
    return {
        "action": "deliver_world",
        "handover": {
            "dead_ends": [filler], "open_questions": [filler],
            "warning": filler,
        },
    }


def test_validate_conclusion_deliver():
    ledger = _FakeLedger(True)
    conclusion, rejection = validate_conclusion(
        _handover(3), ledger=ledger)
    assert conclusion is not None and rejection == ""
    assert conclusion["kind"] == "deliver"


def test_validate_conclusion_requires_state_on_file():
    ledger = _FakeLedger(False)
    conclusion, rejection = validate_conclusion(
        _handover(3), ledger=ledger)
    assert conclusion is None
    assert "state" in rejection


def test_validate_conclusion_handover_caps():
    ledger = _FakeLedger(True)
    per_section = _HANDOVER_HARD_WORD_CAP // 3 + 10
    action = _handover(per_section)
    conclusion, rejection = validate_conclusion(action, ledger=ledger)
    assert conclusion is None and "hard cap" in rejection
    # the degraded-delivery escape valve
    action["handover_compliant"] = False
    conclusion, _ = validate_conclusion(action, ledger=ledger)
    assert conclusion is not None
    assert conclusion["handover_compliant"] is False


def test_validate_conclusion_handover_shape():
    ledger = _FakeLedger(True)
    bad = {"action": "deliver_world", "handover": {
        "dead_ends": [], "open_questions": ["q"], "warning": "w"}}
    conclusion, rejection = validate_conclusion(bad, ledger=ledger)
    assert conclusion is None and "dead_ends" in rejection


def test_validate_conclusion_abstain():
    ledger = _FakeLedger(True)
    ok = {"action": "abstain", "reason": "no ore",
          "axes_checked": ["axis: checked empty"]}
    conclusion, rejection = validate_conclusion(ok, ledger=ledger)
    assert conclusion is not None
    assert conclusion["kind"] == "abstain"
    no_axes = {"action": "abstain", "reason": "no ore"}
    conclusion, rejection = validate_conclusion(no_axes, ledger=ledger)
    assert conclusion is None and "axes_checked" in rejection


# --- dual-spelling paths -----------------------------------------------------


def _world(tmp_path: Path) -> LocalWorld:
    work = tmp_path / "w"
    work.mkdir()
    scratch = tmp_path / "s"
    scratch.mkdir()
    (work / "note.txt").write_text("hello\n", encoding="utf-8")
    return LocalWorld(
        work=work, repo=tmp_path, scratch=scratch,
        timeout_seconds=10, cap_chars=1000,
    )


def test_read_file_accepts_namespace_and_real_spelling(tmp_path: Path):
    world = _world(tmp_path)
    via_ns = world.execute(
        {"action": "read_file", "path": "/work/note.txt"})
    assert via_ns["ok"] and "hello" in via_ns["content"]
    via_real = world.execute({
        "action": "read_file",
        "path": str(tmp_path / "w" / "note.txt"),
    })
    assert via_real["ok"]


def test_write_file_normalizes_real_paths(tmp_path: Path):
    world = _world(tmp_path)
    reply = world.execute({
        "action": "write_file",
        "path": str(tmp_path / "w" / "real.txt"), "content": "x",
    })
    assert reply["ok"]
    assert (tmp_path / "w" / "real.txt").read_text() == "x"
    # outside every root: refused
    outside = world.execute({
        "action": "write_file", "path": "/etc/passwd", "content": "x",
    })
    assert not outside["ok"]


def test_bash_workdir_normalizes_real_paths(tmp_path: Path):
    world = _world(tmp_path)
    reply = world.execute({
        "action": "bash", "command": "pwd",
        "workdir": str(tmp_path / "w"),
    })
    assert reply["ok"]
    assert "w" in reply["output"]


def test_render_native_boundaries_names_the_roots():
    text = render_native_boundaries("/x/work", "/x/repo", "/x/scratch")
    assert "/x/work" in text and "/x/scratch" in text
    container = render_native_boundaries("/work", "/repo", "/scratch")
    assert "/work" in container


# --- the note instrument ------------------------------------------------------


def test_note_appends_and_reads_back(ledger: LocalLedger):
    assert ledger.read_notes() == ""
    first = ledger.append_note("grid_search is 5% — not the bottleneck")
    assert first["ok"]
    ledger.append_note("carried-seed: +12%, checksum identical")
    notes = ledger.read_notes()
    assert "not the bottleneck" in notes
    assert "checksum identical" in notes
    assert not ledger.append_note("   ")["ok"]


def test_note_dispatch_and_resume_context(tmp_path: Path):
    from scientist.agent import build_system_prompt, dispatch_action

    ledger = LocalLedger(tmp_path / ".scientist")
    reply = dispatch_action(
        {"action": "note",
         "text": "pick_mat is O(M^2) — precompute thresholds"},
        world=None, assistant=None, ledger=ledger,
    )
    assert reply["ok"]
    prompt = build_system_prompt(
        {"goal": "g", "editable_paths": ["src"]},
        notes=ledger.read_notes(),
    )
    assert "precompute thresholds" in prompt
    assert "append-only log" in prompt


# --- async work: dispatch, keep working, report lands later ------------------


def _fake_claude(tmp_path: Path) -> Path:
    """A stand-in claude CLI: reads stdin, prints one stream-json result
    event carrying a fenced digest."""
    event = {
        "type": "result",
        "result": "did the work\n```json\n"
                  '{"diff_summary": "changed a.c", '
                  '"self_report_digest": "ran benches", '
                  '"metrics": {"speed": 2}}\n```',
        "usage": {"total_tokens": 7},
    }
    script = tmp_path / "fake_claude.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\ncat <<'JSONLINE'\n"
        + json.dumps(event) + "\nJSONLINE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_work_async_receipt_then_report(tmp_path: Path):
    import time as _time

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    receipt = assistant.work(
        {"action": "work", "instruction": "instrument the kernel"})
    assert receipt["ok"] and receipt["status"] == "running"
    assert "arrives as its own message" in receipt["note"]

    reports: list[dict] = []
    for _ in range(200):
        reports = assistant.poll()
        if reports:
            break
        _time.sleep(0.05)
    assert reports, "dispatched job never finished"
    report = reports[0]
    assert report["ok"] and report["call_id"] == receipt["call_id"]
    assert report["diff_summary"] == "changed a.c"
    assert report["metrics"] == {"speed": 2}
    digest_path = (world.work / ".scientist" / "assistant"
                   / receipt["call_id"] / "digest.json")
    assert digest_path.exists()
    rows = [json.loads(line)
            for line in ledger.assistant_calls_path.read_text().splitlines()]
    assert any(r.get("status") == "dispatched" for r in rows)
    assert any(r.get("question_digest", "").endswith("ran benches")
               for r in rows)
    # nothing left running
    assert assistant.shutdown() == 0


def test_work_receipt_lists_outstanding(tmp_path: Path):
    import time as _time

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    quick = tmp_path / "quick.sh"
    quick.write_text("#!/bin/sh\ncat >/dev/null\nsleep 5\n", encoding="utf-8")
    quick.chmod(0o755)
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(quick),
                               work_default_minutes=10),
        ledger=ledger, episode_id="t",
    )
    first = assistant.work({"action": "work", "instruction": "a"})
    assert first["outstanding_jobs"] == [first["call_id"]]
    _time.sleep(0.1)
    second = assistant.work({"action": "work", "instruction": "b"})
    assert second["outstanding_jobs"] == [first["call_id"],
                                          second["call_id"]]
    assert "still running" in second["note"]
    assistant.shutdown()


def test_wait_blocks_until_report(tmp_path: Path):
    import time as _time

    from scientist.agent import wait_for_reports
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    # finishes after ~0.6s
    script = tmp_path / "soon.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\nsleep 0.6\ncat <<'JSONLINE'\n"
        + json.dumps({"type": "result", "result": "ok", "usage": {}})
        + "\nJSONLINE\n", encoding="utf-8")
    script.chmod(0o755)
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(script),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    assistant.work({"action": "work", "instruction": "quick job"})
    started = _time.time()
    observation = wait_for_reports(assistant, timeout_seconds=30.0)
    assert observation["ok"] and observation.get("landed") == 1
    assert _time.time() - started >= 0.5
    # the single intake point finalizes and returns the report
    reports = assistant.poll()
    assert reports and reports[0]["ok"]
    # nothing outstanding anymore: a further wait times out honestly
    observation = wait_for_reports(assistant, timeout_seconds=0.2)
    assert observation.get("timeout") is True
    assert "still running" in observation["note"] or observation["note"]


def test_wait_report_never_orphans_tool_result(tmp_path: Path):
    """demo-2's death: a report delivered DURING a wait lands as a user
    message between the assistant tool_calls message and its tool
    result — a wire-invariant 400. Drive the real loop: work, wait
    (report lands mid-wait), then one more model call whose incoming
    messages must satisfy the invariant."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    # finishes ~0.5s after dispatch — during the wait turn
    script = tmp_path / "soon.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\nsleep 0.5\ncat <<'JSONLINE'\n"
        + json.dumps({"type": "result", "result": "ok", "usage": {}})
        + "\nJSONLINE\n", encoding="utf-8")
    script.chmod(0o755)
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(script),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )

    class ScriptedModel:
        def __init__(self):
            self.turn = 0
            self.seen: list[list[dict]] = []

        def complete(self, *, system, messages, timeout_seconds, tools):
            self.seen.append([dict(m) for m in messages])
            self.turn += 1
            if self.turn == 1:
                return ModelReply(text="dispatch", tool_calls=(
                    ToolCall(id="c1", name="work",
                             arguments={"instruction": "instrument"}),))
            if self.turn == 2:
                return ModelReply(text="wait", tool_calls=(
                    ToolCall(id="c2", name="wait",
                             arguments={"timeout_seconds": 30}),))
            if self.turn == 3:
                return ModelReply(text="state", tool_calls=(
                    ToolCall(id="c3", name="update_research_state",
                             arguments={"working_model": "m"}),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c4", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]}),))

    model = ScriptedModel()
    result = run_episode(
        model=model, system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=6, wall_seconds=60.0,
    )
    assert result["outcome"] == "abstain"

    # The messages seen by the call AFTER the wait turn: find the wait
    # assistant message and assert its tool result follows immediately.
    after_wait = model.seen[2]
    idx = next(i for i, m in enumerate(after_wait)
               if m.get("role") == "assistant" and m.get("tool_calls")
               and m["tool_calls"][0]["function"]["name"] == "wait")
    assert after_wait[idx + 1]["role"] == "tool", (
        "user message sits between the wait tool_calls and its result "
        "— the demo-2 wire bug")
    delivered = [
        m for m in after_wait[idx + 2:]
        if m.get("role") == "user" and "assistant finished" in str(
            m.get("content"))
    ]
    assert delivered, "the report never landed as its own message"


def test_work_shutdown_abandoned(tmp_path: Path):
    import time as _time

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    sleeper = tmp_path / "sleep_claude.sh"
    sleeper.write_text("#!/bin/sh\ncat >/dev/null\nsleep 30\n",
                       encoding="utf-8")
    sleeper.chmod(0o755)
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(sleeper),
                               work_default_minutes=10),
        ledger=ledger, episode_id="t",
    )
    receipt = assistant.work({"action": "work", "instruction": "slow job"})
    assert receipt["status"] == "running"
    _time.sleep(0.2)
    assert assistant.poll() == []
    assert assistant.shutdown() == 1
    rows = [json.loads(line)
            for line in ledger.assistant_calls_path.read_text().splitlines()]
    assert any(r.get("status") == "abandoned" for r in rows)


# --- compaction (native wire: never orphan a tool result) --------------------


def _turn(i: int) -> list[dict]:
    return [
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": f"c{i}", "name": "bash"}]},
        {"role": "tool", "tool_call_id": f"c{i}",
         "content": "x" * 500},
    ]


def test_compact_never_orphans_tool_results():
    from scientist.agent import _compact_native

    messages = [{"role": "user", "content": "framing"}]
    for i in range(8):
        messages.extend(_turn(i))
    _compact_native(messages, keep_messages=4, max_chars=10_000)
    assert messages[0] == {"role": "user", "content": "framing"}
    # every tool message directly follows its assistant tool_calls message
    for index, message in enumerate(messages):
        if message.get("role") == "tool":
            prev = messages[index - 1]
            assert prev.get("role") == "assistant"
            ids = {
                tc.get("id") for tc in prev.get("tool_calls") or ()
            }
            assert message.get("tool_call_id") in ids
    # and it actually compacted (8 turns -> recent tail only)
    assert sum(1 for m in messages if m.get("role") == "assistant") < 8


def test_compact_keeps_final_turn_even_over_char_budget():
    from scientist.agent import _compact_native

    messages = [{"role": "user", "content": "framing"}]
    for i in range(4):
        messages.extend(_turn(i))
    _compact_native(messages, keep_messages=2, max_chars=10)
    roles = [m["role"] for m in messages]
    assert roles[-2:] == ["assistant", "tool"]


# --- StdlibChatModel ---------------------------------------------------------


class _FakeResponse(io.BytesIO):
    def close(self):  # keep pyupgrade honest: io.BytesIO closes fine
        super().close()


def _sse_stream(*chunks: dict) -> _FakeResponse:
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps(chunk))
    lines.append("data: [DONE]")
    return _FakeResponse("\n".join(lines).encode("utf-8"))


def _tool_fragment_chunks():
    return (
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_0", "type": "function",
             "function": {"name": "bash", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"comma'}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'nd":"ls"}'}}]}}]},
        {"choices": [{"index": 0, "finish_reason": "tool_calls",
                      "delta": {}}]},
        {"choices": [], "usage": {"prompt_tokens": 9,
                                  "completion_tokens": 5}},
    )


def test_stdlib_model_assembles_stream(tmp_path: Path, monkeypatch):
    model = StdlibChatModel(
        model="m", base_url="https://example.invalid", api_key="k")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        return _sse_stream(*_tool_fragment_chunks())

    monkeypatch.setattr("scientist.model_stdlib.urllib.request.urlopen",
                        fake_urlopen)
    reply = model.complete(
        system="s", messages=[{"role": "user", "content": "u"}],
        timeout_seconds=30,
        tools=[{"type": "function", "function": {
            "name": "bash", "parameters": {}}}],
    )
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer k"
    body = captured["body"]
    assert body["stream"] is True and body["tools"]
    assert body["messages"][0] == {"role": "system", "content": "s"}
    assert "response_format" not in body  # tools replace the guard
    (call,) = reply.tool_calls
    assert call.name == "bash"
    assert call.arguments == {"command": "ls"}
    assert reply.usage["completion_tokens"] == 5


def test_stdlib_model_text_track_omits_tools(tmp_path: Path, monkeypatch):
    model = StdlibChatModel(
        model="m", base_url="https://example.invalid", api_key="k")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _sse_stream(
            {"choices": [{"delta": {"content": "hel"}}]},
            {"choices": [{"delta": {"content": "lo"},
                          "finish_reason": "stop"}]},
        )

    monkeypatch.setattr("scientist.model_stdlib.urllib.request.urlopen",
                        fake_urlopen)
    reply = model.complete(
        system="s", messages=[], timeout_seconds=30, json_object=True)
    assert reply.text == "hello"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "tools" not in captured["body"]


def test_stdlib_model_http_error_carries_status(monkeypatch):
    model = StdlibChatModel(
        model="m", base_url="https://example.invalid", api_key="k")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 503, "unavailable", {}, io.BytesIO(b"busy"))

    monkeypatch.setattr("scientist.model_stdlib.urllib.request.urlopen",
                        fake_urlopen)
    from scientist.model import _is_transient

    with pytest.raises(_HttpStatusError) as excinfo:
        model.complete(system="s", messages=[], timeout_seconds=5)
    assert excinfo.value.status_code == 503
    assert _is_transient(excinfo.value)
