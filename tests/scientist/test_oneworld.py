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
    _compact_native,
    _upsert_judgment_message,
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
    reply = ledger.revise_research_judgment({
        "judgment": "binary search dominates",
        "revision_reason": "first probes",
        "evidence_refs": ["src/Simulation.c:120"],
    })
    assert reply["ok"] and reply["revision"] == 1
    assert ledger.state_on_file()
    second = ledger.revise_research_judgment({
        "judgment": "revised", "revision_reason": "new evidence",
        "evidence_refs": [],
    })
    assert second["revision"] == 2
    assert ledger.current_state()["judgment"] == "revised"


def test_current_judgment_may_be_absent(ledger: LocalLedger):
    assert ledger.current_judgment() is None


def test_judgment_revision_is_append_only_and_may_be_uncertain(
    ledger: LocalLedger,
):
    first = ledger.revise_research_judgment({
        "judgment": "No stable mechanism yet; allocation and cache costs remain plausible.",
        "revision_reason": "Initial probes conflict.",
        "evidence_refs": ["experiment:E1"],
    })
    second = ledger.revise_research_judgment({
        "judgment": "Allocation lifetime appears primary; cache cost remains uncertain.",
        "revision_reason": "E2 moved cache cost below 10%.",
        "evidence_refs": ["experiment:E2"],
    })
    assert first["judgment_id"] == "rj-0001"
    assert second["judgment_id"] == "rj-0002"
    assert ledger.current_judgment()["judgment_id"] == "rj-0002"


def test_judgment_history_is_thin_and_detail_is_pull_only(ledger: LocalLedger):
    ledger.revise_research_judgment({
        "judgment": "private long judgment body",
        "revision_reason": "new evidence",
        "evidence_refs": ["experiment:E3"],
    })
    index = ledger.list_research_judgments({"limit": 10})
    assert "judgment" not in index["results"][0]
    detail = ledger.inspect_research_judgment({"judgment_id": "rj-0001"})
    assert detail["judgment"] == "private long judgment body"


def test_revise_research_judgment_validates(ledger: LocalLedger):
    assert not ledger.revise_research_judgment(
        {"judgment": "  ", "revision_reason": "r"})["ok"]
    assert not ledger.revise_research_judgment(
        {"judgment": "m", "revision_reason": "r",
         "evidence_refs": [1]})["ok"]


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


def test_validate_conclusion_does_not_fabricate_state_for_exit():
    ledger = _FakeLedger(False)
    conclusion, rejection = validate_conclusion(
        _handover(3), ledger=ledger)
    assert conclusion is not None
    assert rejection == ""


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
    first = ledger.append_note("grid_search is 5% — not the bottleneck")
    assert first["ok"]
    ledger.append_note("carried-seed: +12%, checksum identical")
    notes = ledger.notes_path.read_text(encoding="utf-8")
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
    prompt = build_system_prompt({"goal": "g", "editable_paths": ["src"]})
    assert "precompute thresholds" not in prompt


def test_judgment_is_an_ordinary_revisable_message_not_system_text():
    messages = [{"role": "user", "content": "begin"}]
    _upsert_judgment_message(messages, {
        "judgment_id": "rj-0001",
        "judgment": "Cache cost and allocation are both plausible.",
        "revision_reason": "evidence conflicts",
        "evidence_refs": ["experiment:E1"],
    })
    assert messages[1]["role"] == "user"
    assert "not an instruction" in messages[1]["content"]
    assert "both plausible" in messages[1]["content"]


def test_judgment_message_is_replaced_and_survives_compaction():
    messages = [{"role": "user", "content": "begin"}]
    _upsert_judgment_message(messages, {
        "judgment_id": "rj-0001", "judgment": "old",
        "revision_reason": "first", "evidence_refs": [],
    })
    _upsert_judgment_message(messages, {
        "judgment_id": "rj-0002", "judgment": "new",
        "revision_reason": "revision", "evidence_refs": [],
    })
    for index in range(8):
        messages.extend([
            {"role": "assistant", "content": f"turn {index}"},
            {"role": "user", "content": f"observation {index}"},
        ])
    _compact_native(messages, keep_messages=4, max_chars=1000)
    blocks = [
        message for message in messages
        if "Current Research View" in str(message.get("content"))
    ]
    assert len(blocks) == 1
    assert "new" in blocks[0]["content"] and "old" not in blocks[0]["content"]


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


def test_executor_engagement_returns_report_when_done(tmp_path: Path):
    """Synchronous seats (round 4): engage blocks to completion and the
    report IS the return value — attributed, digested, and on the
    ledger, with nothing left running after it returns."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    report = assistant.engage("executor", {
        "brief": "instrument the kernel",
        "definition_of_done": "report changes and measurements",
    })
    assert report["ok"] and report["status"] == "done"
    assert report["collaborator_id"].startswith("executor-t-")
    assert report["diff_summary"] == "changed a.c"
    assert report["metrics"] == {"speed": 2}
    digest_path = (world.work / ".scientist" / "assistant"
                   / report["collaborator_id"] / "digest.json")
    assert digest_path.exists()
    rows = [json.loads(line)
            for line in ledger.assistant_calls_path.read_text().splitlines()]
    assert any(r.get("question_digest", "").endswith("ran benches")
               for r in rows)
    # nothing left running: the pid receipt is reaped
    assert not list((world.work / ".scientist" / "assistant")
                    .rglob("proc.pid"))


def test_engagements_run_sequentially_and_leave_nothing_running(
        tmp_path: Path):
    """The sync-era orphan invariant: once engage returns, the seat is
    finished — no background job survives the call, and the next
    engagement gets a fresh, distinct identity."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    first = assistant.engage("executor", {
        "brief": "a", "definition_of_done": "done a",
    })
    second = assistant.engage("searcher", {"brief": "b"})
    assert first["ok"] and second["ok"]
    assert first["collaborator_id"] != second["collaborator_id"]
    assert not list((world.work / ".scientist" / "assistant")
                    .rglob("proc.pid"))


def test_seat_report_is_its_own_tool_result(tmp_path: Path):
    """The sync-era wire invariant (demo-2's death was a report landing
    as a user message between tool_calls and its result): a seat's
    report arrives as the tool result of its OWN call, immediately
    adjacent, and the next model call sees exactly that."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path)),
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
                    ToolCall(id="c1", name="executor", arguments={
                        "brief": "instrument",
                        "definition_of_done": "report the result",
                    }),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c2", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]}),))

    model = ScriptedModel()
    result = run_episode(
        model=model, system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=4, wall_seconds=60.0,
    )
    assert result["outcome"] == "abstain"

    after_dispatch = model.seen[1]
    idx = next(i for i, m in enumerate(after_dispatch)
               if m.get("role") == "assistant" and m.get("tool_calls")
               and m["tool_calls"][0]["function"]["name"] == "executor")
    result_msg = after_dispatch[idx + 1]
    assert result_msg["role"] == "tool", (
        "the seat report did not arrive as the executor call's own tool "
        "result")
    assert result_msg["tool_call_id"] == "c1"
    assert "Research collaborator report" in result_msg["content"]
    assert "ran benches" in result_msg["content"]


def test_wire_log_keeps_forwarded_calls_with_narration(
        tmp_path: Path):
    """v3 probe debt: a turn that narrated AND called a seat once lost
    the delegation's arguments — the record could not say what was
    asked of whom (nor with what time box). The wire log carries both:
    narration in content, the call in tool_calls with raw arguments."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    script = tmp_path / "immediate.sh"
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\ncat <<'JSONLINE'\n"
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

        def complete(self, *, system, messages, timeout_seconds, tools):
            self.turn += 1
            if self.turn == 1:
                args = {
                    "brief": "sweep the flag space",
                    "definition_of_done": "report the best variant",
                    "timeout_minutes": 90,
                }
                return ModelReply(text="delegate the sweep", tool_calls=(
                    ToolCall(id="c1", name="executor", arguments=args,
                             arguments_raw=json.dumps(args)),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c2", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]},
                         arguments_raw=json.dumps(
                             {"reason": "probe",
                              "axes_checked": ["a"]})),))

    from scientist.scientist_session import ScientistSession
    session_dir = world.work / ".scientist" / "session"
    session = ScientistSession.load_or_create(
        session_dir, prompt_version="test-archive", episode_id="t")
    result = run_episode(
        model=ScriptedModel(), system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=4, wall_seconds=60.0, session=session,
    )
    assert result["outcome"] == "abstain"

    turns = [json.loads(l) for l in
             session.wire_path.read_text(encoding="utf-8").splitlines()]
    narrations = [t for t in turns if t.get("role") == "assistant"
                  and t.get("content")]
    assert any("delegate the sweep" in t["content"] for t in narrations), (
        "the narration record was dropped")
    calls = [c for t in narrations for c in t.get("tool_calls") or []
             if c["function"]["name"] == "executor"]
    assert calls, ("the executor call vanished from the wire — the "
                   "record cannot say what was delegated")
    archived_args = json.loads(calls[0]["function"]["arguments"])
    assert archived_args.get("timeout_minutes") == 90, (
        "the wire lost the time box the PI chose")


def test_reconcile_harvests_orphaned_seat_on_startup(tmp_path: Path):
    """The one window sync cannot close: a SIGKILL of the scientist
    itself leaves a seat running with no digest. On the next startup
    _reconcile kills it (pid guarded by 'claude' in the cmdline) and
    harvests a crash-salvaged digest — evidence must survive us."""
    import subprocess

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    orphan_dir = world.work / ".scientist" / "assistant" / "executor-t-007"
    orphan_dir.mkdir(parents=True)
    sleeper = tmp_path / "fake_claude_orphan.sh"
    sleeper.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    sleeper.chmod(0o755)
    proc = subprocess.Popen(
        [str(sleeper)], start_new_session=True)
    (orphan_dir / "proc.pid").write_text(str(proc.pid), encoding="utf-8")
    (orphan_dir / "raw.txt").write_text(
        '{"type": "assistant", "message": {"content": '
        '[{"type": "text", "text": "partial diagnosis in hand"}]}}\n',
        encoding="utf-8")

    InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(sleeper)),
        ledger=ledger, episode_id="t",
    )
    proc.wait(timeout=10)   # killed by the reconcile pass
    digest = json.loads((orphan_dir / "digest.json").read_text())
    assert digest["status"] == "crash-salvaged"
    assert "partial diagnosis in hand" in digest["self_report_digest"]
    # the counter resumed past the orphan: a new call cannot truncate
    # its still-growing raw.txt
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    report = assistant.engage("executor", {
        "brief": "b", "definition_of_done": "d"})
    assert int(report["collaborator_id"].rsplit("-", 1)[1]) > 7


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
