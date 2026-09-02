"""Tests for the oneworld package path: stdlib transport, ledger,
exit-contract validation, dual-spelling paths, boundaries rendering."""
from __future__ import annotations

import io
import json
import time
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


def test_malformed_call_bounces_back_instead_of_killing_the_run(
        tmp_path: Path):
    # observed live in r6: a write_file whose arguments carried content
    # but no path raised KeyError and took the whole episode down — a
    # malformed call is a tool error the model retries, never a crash
    world = _world(tmp_path)
    reply = world.execute({"action": "write_file", "content": "x"})
    assert not reply["ok"]
    assert "path" in reply["error"]
    reply = world.execute({"action": "read_file"})
    assert not reply["ok"]
    assert "path" in reply["error"]
    # and the dispatch-level net catches anything else the narrow
    # branches miss (same law as engagement dispatches)
    from scientist.agent import dispatch_action
    reply = dispatch_action(
        {"action": "write_file", "content": "x"},
        world=world, assistant=None, ledger=None)
    assert not reply["ok"]


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
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
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
    digest_path = (world.scratch
                   / report["collaborator_id"] / "digest.json")
    assert digest_path.exists()
    rows = [json.loads(line)
            for line in ledger.assistant_calls_path.read_text().splitlines()]
    assert any(r.get("question_digest", "").endswith("ran benches")
               for r in rows)
    # nothing left running: the pid receipt is reaped
    assert not list((world.scratch)
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
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    first = assistant.engage("executor", {
        "brief": "a", "definition_of_done": "done a",
    })
    second = assistant.engage("searcher", {"brief": "b"})
    assert first["ok"] and second["ok"]
    assert first["collaborator_id"] != second["collaborator_id"]
    assert not list((world.scratch)
                    .rglob("proc.pid"))


def test_seat_report_is_its_own_tool_result(tmp_path: Path):
    """The sync-era wire invariant (demo-2's death was a report landing
    as a user message between tool_calls and its result) — now carried by
    the one seat that stays synchronous: a REVIEWER's report arrives as
    the tool result of its OWN call, immediately adjacent, and the next
    model call sees exactly that. (Async seats' reports arrive at turn
    tops — see the drain tests.)"""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
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
                    ToolCall(id="c1", name="reviewer", arguments={
                        "brief": "instrument",
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
               and m["tool_calls"][0]["function"]["name"] == "reviewer")
    result_msg = after_dispatch[idx + 1]
    assert result_msg["role"] == "tool", (
        "the reviewer report did not arrive as the reviewer call's own "
        "tool result")
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
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(script),
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
    orphan_dir = world.scratch / "executor-t-007"
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
    # v7: the engagement directory is born at launch — manifest first
    (orphan_dir / "manifest.json").write_text(json.dumps({
        "role": "executor", "collaborator_id": "executor-t-007",
        "box": 60, "started": time.time(), "work_dir": str(world.work),
        "side_dir": None, "mode": "current", "brief": "b",
    }), encoding="utf-8")

    InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(sleeper)),
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
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
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
        model="m", base_url="https://example.invalid", api_key="k",
        max_output_tokens=8192)
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
        model="m", base_url="https://example.invalid", api_key="k",
        max_output_tokens=8192)
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
        model="m", base_url="https://example.invalid", api_key="k",
        max_output_tokens=8192)

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


# --- v7: the engagement is a directory; async dispatch + continuation -------

def _slow_claude(tmp_path: Path, seconds: float,
                 name: str = "fake_claude_slow.sh") -> Path:
    """A fake seat that finishes after ``seconds`` — long enough to span
    a model call, short enough for tests."""
    event = {
        "type": "result",
        "result": "did the work\n```json\n"
                  '{"diff_summary": "changed a.c", '
                  '"self_report_digest": "ran benches", '
                  '"metrics": {"speed": 2}}\n```',
        "usage": {"total_tokens": 7},
    }
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        f"sleep {seconds}\n"
        "cat <<'JSONLINE'\n" + json.dumps(event) + "\nJSONLINE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _session_claude(tmp_path: Path) -> Path:
    """A fake seat that emits an init line carrying a session_id, prints
    the standard result, and records its argv and cwd to $SEAT_OUT —
    the assertion surface for --resume and workspace reuse."""
    init = {"type": "system", "subtype": "init",
            "session_id": "sess-abc-123"}
    event = {
        "type": "result",
        "result": "did the work\n```json\n"
                  '{"diff_summary": "changed a.c", '
                  '"self_report_digest": "ran benches", '
                  '"metrics": {"speed": 2}}\n```',
        "usage": {"total_tokens": 7},
    }
    script = tmp_path / "fake_claude_session.sh"
    script.write_text(
        "#!/bin/sh\n"
        'cat > /dev/null\n'
        'printf \'%s\\n\' ' + json.dumps(json.dumps(init)) + '\n'
        'printf \'%s\\n\' "$@" > "${SEAT_OUT}/argv.txt"\n'
        'printf \'%s\\n\' "$PWD" > "${SEAT_OUT}/pwd.txt"\n'
        'cat <<\'JSONLINE\'\n' + json.dumps(event) + "\nJSONLINE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _sleeper_claude(tmp_path: Path) -> Path:
    """A fake seat that emits its init line, one substantive assistant
    event, then sleeps past any test — the occupant for time-box and
    exit-salvage paths."""
    init = {"type": "system", "subtype": "init",
            "session_id": "sess-sleeper-1"}
    assistant_event = {
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": "partial diagnosis in hand"}]},
    }
    script = tmp_path / "fake_claude_sleeper.sh"
    script.write_text(
        "#!/bin/sh\n"
        'cat > /dev/null\n'
        'printf \'%s\\n\' ' + json.dumps(json.dumps(init)) + '\n'
        'printf \'%s\\n\' ' + json.dumps(json.dumps(assistant_event)) + '\n'
        'sleep 30\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_launch_writes_manifest_and_prompt_immediately(tmp_path: Path):
    """The engagement directory is BORN at launch: manifest (with the
    box and workspace), prompt, and pid exist before any collection;
    there is no digest yet — the state machine is file existence."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_sleeper_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    ack = assistant.engage_async("executor", {
        "brief": "b", "definition_of_done": "d"})
    assert ack["ok"] and ack["status"] == "running"
    d = world.scratch / ack["collaborator_id"]
    mani = json.loads((d / "manifest.json").read_text())
    assert mani["role"] == "executor" and mani["box"] == 60
    assert (d / "prompt.txt").exists() and (d / "proc.pid").exists()
    assert not (d / "digest.json").exists()
    rows = assistant.pending()
    assert [r["collaborator_id"] for r in rows] == [ack["collaborator_id"]]
    assistant.shutdown_pending()


def test_async_engagement_completes_later_and_leaves_no_orphans(
        tmp_path: Path):
    """Fire-and-return: the ack comes back at once; the report arrives
    through poll_completions once the seat finishes; nothing is left
    running and the ledger carries the call."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_slow_claude(tmp_path, 0.4)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    ack = assistant.engage_async("executor", {
        "brief": "b", "definition_of_done": "d"})
    assert ack["status"] == "running"
    assert assistant.poll_completions() == []
    time.sleep(0.8)
    reports = assistant.poll_completions()
    assert len(reports) == 1
    assert reports[0]["status"] == "done"
    assert reports[0]["self_report_digest"] == "ran benches"
    d = world.scratch / ack["collaborator_id"]
    digest = json.loads((d / "digest.json").read_text())
    assert digest["status"] == "done"
    assert not (d / "proc.pid").exists()
    # exactly-once: the marker is down, a second poll has nothing
    assert assistant.poll_completions() == []
    assert (d / "read.marker").exists()


def test_two_concurrent_async_seats_and_wait_returns_both(tmp_path: Path):
    """Independent seats run concurrently; wait blocks until both finish
    and returns their reports together."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_slow_claude(tmp_path, 0.4)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    ack1 = assistant.engage_async("executor", {
        "brief": "b1", "definition_of_done": "d"})
    ack2 = assistant.engage_async("proposer", {"brief": "b2",
                                               "scope": "open"})
    assert ack1["collaborator_id"] != ack2["collaborator_id"]
    assert len(assistant.pending()) == 2
    result = assistant.wait_for_seats()
    assert result["ok"] and result["still_running"] == []
    ids = sorted(r["collaborator_id"] for r in result["finished"])
    assert ids == sorted([ack1["collaborator_id"],
                          ack2["collaborator_id"]])


def test_wait_with_nothing_pending_returns_immediately(tmp_path: Path):
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    t0 = time.monotonic()
    result = assistant.wait_for_seats()
    assert result["finished"] == [] and result["still_running"] == []
    assert "no engagement pending" in result.get("note", "")
    assert time.monotonic() - t0 < 2.0


def test_async_timeout_salvage_via_sweep(tmp_path: Path):
    """The box is enforced by sweep: a seat past its box is killed and
    collected as timeout-salvaged, with the status a FIELD in the digest
    and the session id already captured from the init line."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_sleeper_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    ack = assistant.engage_async("executor", {
        "brief": "b", "definition_of_done": "d"})
    d = world.scratch / ack["collaborator_id"]
    # let the seat flush its events before forcing the box to expiry
    # (killing faster than the shell prints would salvage nothing)
    for _ in range(100):
        try:
            if "partial diagnosis" in (d / "raw.txt").read_text():
                break
        except OSError:
            pass
        time.sleep(0.05)
    mani = json.loads((d / "manifest.json").read_text())
    mani["box"] = 0          # white-box: the box is spent
    (d / "manifest.json").write_text(json.dumps(mani))
    reports = assistant.sweep()
    assert len(reports) == 1
    assert reports[0]["status"] == "timeout-salvaged"
    digest = json.loads((d / "digest.json").read_text())
    assert digest["status"] == "timeout-salvaged"
    assert digest["session_id"] == "sess-sleeper-1"
    assert not (d / "proc.pid").exists()


def test_episode_exit_salvages_pending_seats(tmp_path: Path):
    """Episode exit leaves nothing running: a pending seat is killed,
    crash-salvaged, and its report lands in the wire as the last word."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_sleeper_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )

    class ScriptedModel:
        def __init__(self):
            self.turn = 0

        def complete(self, *, system, messages, timeout_seconds, tools):
            self.turn += 1
            if self.turn == 1:
                return ModelReply(text="dispatch", tool_calls=(
                    ToolCall(id="c1", name="executor", arguments={
                        "brief": "b", "definition_of_done": "d"}),))
            # let the dispatched seat flush its events before the exit
            # salvage kills it (an empty transcript salvages nothing)
            time.sleep(0.8)
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c2", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]}),))

    result = run_episode(
        model=ScriptedModel(), system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=4, wall_seconds=60.0,
    )
    assert result["outcome"] == "abstain"
    base = world.scratch
    assert not list(base.rglob("proc.pid"))
    salvaged = [json.loads(p.read_text()) for p in base.glob(
        "*/digest.json")]
    assert any(s["status"] == "crash-salvaged" for s in salvaged)


def test_turn_top_drain_emits_user_role_report(tmp_path: Path):
    """A finished async engagement's report arrives as a USER-role
    observation at the top of a later turn — never between a tool_call
    and its own result (the compaction adjacency rule)."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_slow_claude(tmp_path, 0.3)),
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
                        "brief": "b", "definition_of_done": "d"}),))
            if self.turn == 2:
                time.sleep(1.0)     # the PI thinks; the seat finishes
                return ModelReply(text="probe", tool_calls=(
                    ToolCall(id="c2", name="bash",
                             arguments={"command": "true"}),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c3", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]}),))

    model = ScriptedModel()
    result = run_episode(
        model=model, system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=5, wall_seconds=60.0,
    )
    assert result["outcome"] == "abstain"
    third = model.seen[2]
    drained = [m for m in third
               if m.get("role") == "user"
               and "Research collaborator report" in str(m.get("content"))]
    assert drained, "no drained report at the third turn's top"
    assert "ran benches" in drained[-1]["content"]


def test_wait_returns_reports_exactly_once(tmp_path: Path):
    """wait's own tool result carries the pending reports, and the
    turn-top drain does not re-deliver them (read.marker idempotence)."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall
    from scientist.scientist_session import ScientistSession

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_slow_claude(tmp_path, 0.4)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    session = ScientistSession.load_or_create(
        world.work / ".scientist" / "session", prompt_version="t",
        episode_id="t")

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
                        "brief": "b", "definition_of_done": "d"}),))
            if self.turn == 2:
                return ModelReply(text="collect", tool_calls=(
                    ToolCall(id="c2", name="wait", arguments={}),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c3", name="abstain",
                         arguments={"reason": "probe",
                                    "axes_checked": ["a"]}),))

    result = run_episode(
        model=ScriptedModel(), system_prompt="sys",
        messages=[{"role": "user", "content": "go"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=5, wall_seconds=60.0, session=session,
    )
    assert result["outcome"] == "abstain"
    wire_lines = session.wire_path.read_text().splitlines()
    # one delivery, one wire line (the report dict carries the digest
    # string under two field names — count lines, not substrings)
    assert sum(1 for line in wire_lines if "ran benches" in line) == 1, (
        "the report must be delivered exactly once across wait and drains")
    second = [m for m in [json.loads(line)
                          for line in session.wire_path.read_text().splitlines()]
              if m.get("role") == "tool" and m.get("tool_call_id") == "c2"]
    assert second and "ran benches" in second[0]["content"]


def test_same_turn_batch_returns_adjacent_acks(tmp_path: Path):
    """Two seats dispatched in one turn each get their acknowledgment
    as their OWN call's tool result, immediately adjacent."""
    from scientist.agent import run_episode
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.model import ModelReply, ToolCall

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_slow_claude(tmp_path, 0.4)),
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
                        "brief": "b1", "definition_of_done": "d"}),
                    ToolCall(id="c2", name="proposer", arguments={
                        "brief": "b2", "scope": "open"}),))
            return ModelReply(text="stop", tool_calls=(
                ToolCall(id="c3", name="abstain",
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
    second = model.seen[1]
    dispatch = next(m for m in second
                    if m.get("role") == "assistant" and m.get("tool_calls"))
    calls = dispatch["tool_calls"]
    for i, call in enumerate(calls):
        result_msg = second[second.index(dispatch) + 1 + i]
        assert result_msg["role"] == "tool"
        assert result_msg["tool_call_id"] == call["id"]
        assert '"running"' in result_msg["content"]


def test_continue_engagement_passes_resume_and_old_workspace(
        tmp_path: Path):
    """Continuation is claude --resume <session-id> in the engagement's
    ORIGINAL workspace, recorded as continued_from in the new report."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    seat_out = tmp_path / "seatout"
    seat_out.mkdir()
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_session_claude(tmp_path)),
                               env={"SEAT_OUT": str(seat_out)},
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    first = assistant.engage("executor", {
        "brief": "b1", "definition_of_done": "d",
        "workspace": "isolated"})
    assert first["ok"] and first["status"] == "done"
    old_id = first["collaborator_id"]
    old_fork = world.scratch / old_id / "world"
    assert old_fork.is_dir()

    ack = assistant.continue_engagement({
        "collaborator_id": old_id,
        "brief": "the world changed: X landed; continue with Y",
        "definition_of_done": "report the delta",
    })
    assert ack["ok"] and ack["status"] == "running"
    assert ack["continued_from"] == old_id
    result = assistant.wait_for_seats()
    reports = result["finished"]
    assert any(r.get("continued_from") == old_id for r in reports)
    argv = (seat_out / "argv.txt").read_text()
    assert "--resume" in argv and "sess-abc-123" in argv
    pwd = (seat_out / "pwd.txt").read_text().strip()
    assert pwd == str(old_fork)


def test_continue_engagement_rejections(tmp_path: Path):
    """Every rejection is a clean receipt with a humane error: the
    validation chain runs entirely on the engagement records."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    base = world.scratch
    base.mkdir(parents=True, exist_ok=True)
    (base / "reviewer-t-001").mkdir()
    (base / "reviewer-t-001" / "digest.json").write_text(json.dumps(
        {"role": "reviewer", "status": "done", "session_id": "s"}))
    (base / "executor-t-002").mkdir()
    (base / "executor-t-002" / "digest.json").write_text(json.dumps(
        {"role": "executor", "status": "failed"}))
    (base / "executor-t-003").mkdir()
    (base / "executor-t-003" / "digest.json").write_text(json.dumps(
        {"role": "executor", "status": "done", "session_id": ""}))
    (base / "executor-t-004").mkdir()
    (base / "executor-t-004" / "digest.json").write_text(json.dumps(
        {"role": "executor", "status": "done", "session_id": "s9"}))
    (base / "executor-t-004" / "manifest.json").write_text(json.dumps(
        {"work_dir": "/nonexistent/reclaimed-ws", "side_dir": None,
         "mode": "isolated"}))
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    brief = {"brief": "b", "definition_of_done": "d"}
    r1 = assistant.continue_engagement(
        {"collaborator_id": "reviewer-t-001", **brief})
    assert not r1["ok"] and "only Executor" in r1["error"]
    r2 = assistant.continue_engagement(
        {"collaborator_id": "executor-t-002", **brief})
    assert not r2["ok"] and "salvageable" in r2["error"]
    r3 = assistant.continue_engagement(
        {"collaborator_id": "executor-t-003", **brief})
    assert not r3["ok"] and "no session id" in r3["error"]
    r4 = assistant.continue_engagement(
        {"collaborator_id": "executor-t-999", **brief})
    assert not r4["ok"] and "no finished engagement record" in r4["error"]
    r5 = assistant.continue_engagement(
        {"collaborator_id": "executor-t-004", **brief})
    assert not r5["ok"] and "reclaimed" in r5["error"]


def test_reviewer_heard_after_reads_finished_at_field(tmp_path: Path):
    """The listen door reads the recorded finished_at (not the file's
    mtime) when present — the field is the explicit carrier; mtime is
    the fallback for records written before the field existed."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    base = world.scratch
    base.mkdir(parents=True, exist_ok=True)
    (base / "reviewer-t-001").mkdir()
    (base / "reviewer-t-001" / "digest.json").write_text(json.dumps({
        "role": "reviewer", "status": "done",
        "finished_at": 5000.0,
    }))
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    assert not assistant.reviewer_heard_after(6000.0)
    assert assistant.reviewer_heard_after(4000.0)


def _speed_by_brief_claude(tmp_path: Path) -> Path:
    """A fake seat whose duration its brief chooses: a brief containing
    SLOW sleeps past any test, any other brief finishes at once — one
    config, two speeds, the surface wait(mode=any) needs."""
    event = {
        "type": "result",
        "result": "did the work\n```json\n"
                  '{"diff_summary": "changed a.c", '
                  '"self_report_digest": "ran benches", '
                  '"metrics": {"speed": 2}}\n```',
        "usage": {"total_tokens": 7},
    }
    script = tmp_path / "fake_claude_speed_by_brief.sh"
    script.write_text(
        "#!/bin/sh\n"
        "cat > brief.$$\n"
        "if grep -q SLOW brief.$$; then sleep 30; else sleep 0.3; fi\n"
        "rm -f brief.$$\n"
        "cat <<'JSONLINE'\n" + json.dumps(event) + "\nJSONLINE\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_wait_any_returns_first_arrival_while_rest_run(tmp_path: Path):
    """mode=any: with a fast seat and a still-running slow seat in
    flight, wait returns the fast report WITHOUT holding it for the slow
    one — harvest-early semantics, the fix for a PI that would otherwise
    learn not to speculate next to a long mainline engagement."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_speed_by_brief_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    slow = assistant.engage_async("executor", {
        "brief": "SLOW mainline leg", "definition_of_done": "d"})
    fast = assistant.engage_async("executor", {
        "brief": "quick speculative probe", "definition_of_done": "d"})
    result = assistant.wait_for_seats(timeout_minutes=1, mode="any")
    assert result["mode"] == "any"
    ids = [r["collaborator_id"] for r in result["finished"]]
    assert fast["collaborator_id"] in ids
    assert slow["collaborator_id"] not in ids
    assert [r["collaborator_id"]
            for r in result["still_running"]] == [slow["collaborator_id"]]
    slow_dir = (world.scratch
                / slow["collaborator_id"])
    assert (slow_dir / "proc.pid").exists()    # the mainline still runs
    assistant.shutdown_pending()


def test_cancel_engagement_salvages_and_consumes_inline(tmp_path: Path):
    """Stop-loss: a running seat is stopped before its box, its partial
    transcript salvaged as status=cancelled, and the report returned as
    the call's OWN result — read.marker touched, so a later turn top
    will not re-deliver it. Finished and unknown ids are clean errors."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_sleeper_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    ack = assistant.engage_async("executor", {
        "brief": "b", "definition_of_done": "d"})
    time.sleep(0.5)     # let the init line + partial text flush
    report = assistant.cancel_engagement({
        "collaborator_id": ack["collaborator_id"],
        "reason": "eclipsed by a sibling result"})
    assert report["ok"] and report["status"] == "cancelled"
    assert "eclipsed" in str(report.get("note") or "")
    assert report.get("self_report_digest") == "partial diagnosis in hand"
    d = world.scratch / ack["collaborator_id"]
    assert (d / "digest.json").exists()
    assert not (d / "proc.pid").exists()
    assert (d / "read.marker").exists()       # consumed inline
    assert assistant.poll_completions() == []  # nothing re-delivered
    again = assistant.cancel_engagement({
        "collaborator_id": ack["collaborator_id"]})
    assert not again["ok"] and "already finished" in again["error"]
    missing = assistant.cancel_engagement(
        {"collaborator_id": "executor-t-999"})
    assert not missing["ok"]


def test_seat_runtime_is_world_scoped(tmp_path: Path, monkeypatch):
    """Run-by-run isolation (2026-09-01 ruling): a run's world opens
    brand new, sees nothing outside itself, and the channel is exactly
    the spec's. The seat subprocess runs with the run world's own .claude
    and home — never the user's ~/.claude (whose settings.json env block
    the CLI applies OVER process env) and never an inherited session
    identity."""
    import os

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    # ambient user/session state that must NOT reach the seat
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "leak-session")
    monkeypatch.setenv("CLAUDE_EFFORT", "high")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://user.example")

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")

    def _env_claude(tmp: Path) -> Path:
        script = tmp / "env_claude.sh"
        script.write_text(
            "#!/bin/sh\n"
            "env | sort > \"$ENV_DUMP\"\n"
            "cat <<'JSONLINE'\n"
            + json.dumps({
                "type": "result",
                "result": "ok\n```json\n{\"diff_summary\": \"\"}\n```",
                "usage": {}}) + "\nJSONLINE\n",
            encoding="utf-8")
        script.chmod(0o755)
        return script

    dump = tmp_path / "seat_env.txt"
    monkeypatch.setenv("ENV_DUMP", str(dump))
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_env_claude(tmp_path)),
                               work_default_minutes=1,
                               env={"ANTHROPIC_AUTH_TOKEN": "sk-spec"}),
        ledger=ledger, episode_id="iso-run",
    )
    report = assistant.engage("executor", {
        "brief": "b", "definition_of_done": "d"})
    assert report["ok"]
    seen = dict(
        line.split("=", 1) for line in
        dump.read_text().splitlines() if "=" in line)
    # the world's runtime, and nothing of the user's
    assert seen["CLAUDE_CONFIG_DIR"] == str(world.scratch / ".claude")
    assert seen["HOME"] == str(world.scratch / "home")
    assert seen.get("ANTHROPIC_AUTH_TOKEN") == "sk-spec"
    for key in seen:
        if key != "CLAUDE_CONFIG_DIR":      # ours, set above
            assert not key.startswith("CLAUDE"), key
        assert key != "ANTHROPIC_BASE_URL" or \
            seen[key] != "https://user.example"
    # the runtime itself: fresh settings carrying only the spec env,
    # and the run's own git identity
    settings = json.loads(
        (world.scratch / ".claude" / "settings.json").read_text())
    assert settings == {"env": {"ANTHROPIC_AUTH_TOKEN": "sk-spec"}}
    assert "iso-run" in (world.scratch / "home" / ".gitconfig").read_text()


def test_seat_prompt_states_its_fuse(tmp_path: Path):
    """The worker must see its runway: a seat that cannot see its fuse
    cannot pace to it (commit checkpoints and measurement passes price
    differently against 15 minutes vs 3 hours — the shakedown run's
    executor-003 was salvaged mid-work with no warning in its prompt).
    Stated as fact, never as an instruction."""
    from scientist.assistant_tools import AssistantConfig, InWorldAssistant

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium", command=str(_fake_claude(tmp_path)),
                               work_default_minutes=1),
        ledger=ledger, episode_id="t",
    )
    report = assistant.engage("executor", {
        "brief": "b", "definition_of_done": "d",
        "timeout_minutes": 30})
    assert report["ok"]
    prompt = (world.scratch / report["collaborator_id"]
              / "prompt.txt").read_text()
    assert "Fuse: about 30 minutes" in prompt
    assert "survive a salvage" in prompt
