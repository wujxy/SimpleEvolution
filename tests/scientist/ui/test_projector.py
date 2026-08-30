from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.ui.projector import RunProjector, summarize_seat_record
from scientist.ui.reader import (
    ReaderBatch, RunLayout, RunReader, SourceRecord,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _seat_record(tmp_path: Path, offset: int, value: object) -> SourceRecord:
    raw = (json.dumps(value) + "\n").encode()
    return SourceRecord(
        id=f"seat:executor-1:{offset}",
        source="seat:executor-1",
        path=tmp_path / "raw.txt",
        offset=offset,
        length=len(raw),
        raw=raw,
        value=value,
        is_json=True,
    )


def test_projector_keeps_recovery_and_seat_truth_separate(run_fixture):
    run_dir, scientist = run_fixture
    (run_dir / "run.log").write_text(
        "[scientist 18:50:56] step 8/3000: thinking\n"
        "[scientist] model failure: connection failure: TLS EOF\n"
        "[supervisor] infra crash (attempt 1, rc=1) — resuming from wire in 60s\n"
        "[scientist] resumed from wire.jsonl: 22 messages replayed\n"
        "[scientist 18:51:57] step 1/3000: thinking\n",
        encoding="utf-8",
    )
    executor = scientist / "assistant" / "executor-ep-001"
    searcher = scientist / "assistant" / "searcher-ep-002"
    _write_json(executor / "manifest.json", {
        "role": "executor",
        "collaborator_id": "executor-ep-001",
        "started": 1000.0,
        "box": 7200,
        "brief": "profile hot loop",
    })
    (executor / "raw.txt").write_text("", encoding="utf-8")
    _write_json(searcher / "manifest.json", {
        "role": "searcher",
        "collaborator_id": "searcher-ep-002",
        "started": 1001.0,
        "box": 3600,
        "brief": "inspect getters",
    })
    _write_json(searcher / "digest.json", {
        "status": "done",
        "finished_at": 1200.0,
        "report_digest": "virtual getters are indirect calls",
    })
    (searcher / "read.marker").touch()

    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())
    projector.apply(reader.poll())
    snapshot = projector.snapshot()

    assert [attempt["number"] for attempt in snapshot["attempts"]] == [1, 2]
    assert snapshot["run"]["formal_status"] == "unconcluded"
    assert snapshot["run"]["current_activity"] == "thinking"
    assert snapshot["seats"]["executor-ep-001"]["formal_status"] == "started"
    assert snapshot["seats"]["searcher-ep-002"]["formal_status"] == "done"
    assert snapshot["seats"]["searcher-ep-002"]["delivered"] is True


@pytest.mark.parametrize(
    ("outcome", "formal_status"),
    [
        ("deliver", "delivered"),
        ("abstain", "abstained"),
        ("cut_off", "cut_off"),
        ("crashed", "crashed"),
    ],
)
def test_current_conclusion_sets_formal_run_status(
        run_fixture, outcome, formal_status):
    run_dir, scientist = run_fixture
    _write_json(scientist / "conclusion.json", {
        "outcome": outcome,
        "finished_at": "2026-08-30T20:00:00",
    })
    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())

    projector.apply(reader.poll())

    assert projector.snapshot()["run"]["formal_status"] == formal_status


def test_historical_crash_does_not_mark_current_run_crashed(run_fixture):
    run_dir, scientist = run_fixture
    _write_json(scientist / "conclusion.0830.attempt1.crashed.json", {
        "outcome": "crashed",
        "finished_at": "2026-08-30T18:50:56",
    })
    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())

    projector.apply(reader.poll())

    assert projector.snapshot()["run"]["formal_status"] == "unconcluded"
    assert any(
        item["kind"] == "attempt_crashed"
        for item in projector.snapshot()["timeline"]
    )


def test_latest_research_judgment_and_usage_are_projected(run_fixture):
    run_dir, scientist = run_fixture
    (scientist / "research_state.jsonl").write_text(
        '{"judgment_id":"rj-1","judgment":"first"}\n'
        '{"judgment_id":"rj-2","working_model":"second","evidence_refs":["R1"]}\n',
        encoding="utf-8",
    )
    (scientist / "usage.jsonl").write_text(
        '{"total_tokens":10}\n{"total_tokens":15}\n',
        encoding="utf-8",
    )
    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())

    projector.apply(reader.poll())
    snapshot = projector.snapshot()

    assert snapshot["run"]["current_judgment"]["judgment"] == "second"
    assert snapshot["run"]["current_judgment"]["evidence_refs"] == ["R1"]
    assert snapshot["usage"] == {"calls": 2, "total_tokens": 25}


def test_large_initial_raw_history_is_indexed_progressively(run_fixture):
    run_dir, scientist = run_fixture
    seat = scientist / "assistant" / "executor-ep-001"
    _write_json(seat / "manifest.json", {
        "role": "executor",
        "collaborator_id": "executor-ep-001",
        "started": 1000.0,
        "box": 7200,
    })
    line = json.dumps({"type": "system", "payload": "x" * 2000}) + "\n"
    (seat / "raw.txt").write_text(line * 1200, encoding="utf-8")
    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())

    projector.apply(reader.poll())
    first = projector.snapshot()
    assert first["seats"]["executor-ep-001"]["formal_status"] == "started"
    assert first["indexing"] is True

    for _ in range(5):
        projector.apply(reader.poll())
        if not projector.snapshot()["indexing"]:
            break

    assert projector.snapshot()["indexing"] is False


def test_bash_tool_summary_names_benchmark_without_running_it(tmp_path):
    record = _seat_record(tmp_path, 10, {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "id": "tool-1",
            "name": "Bash",
            "input": {
                "command": "bash scripts/benchmark.sh --evtmax 10",
            },
        }]},
    })

    activity = summarize_seat_record(record)

    assert activity["summary"] == "运行 benchmark.sh --evtmax 10"
    assert activity["status"] == "running"
    assert activity["tool_use_id"] == "tool-1"


def test_tool_progress_and_result_update_one_activity(tmp_path):
    records = [
        _seat_record(tmp_path, 10, {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "tool-1",
                "name": "Bash",
                "input": {"command": "pytest -q"},
            }]},
        }),
        _seat_record(tmp_path, 20, {
            "type": "tool_progress",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "elapsed_time_seconds": 5,
        }),
        _seat_record(tmp_path, 30, {
            "type": "tool_progress",
            "tool_use_id": "tool-1",
            "tool_name": "Bash",
            "elapsed_time_seconds": 10,
        }),
        _seat_record(tmp_path, 40, {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "is_error": False,
                "content": "16 passed",
            }]},
        }),
    ]
    projector = RunProjector({})

    projector.apply(ReaderBatch(records, [], initial_index_complete=True))

    activities = projector.snapshot()["seats"]["executor-1"]["activities"]
    assert len(activities) == 1
    assert activities[0]["summary"] == "运行 pytest -q"
    assert activities[0]["status"] == "succeeded"
    assert len(activities[0]["detail_refs"]) == 4


def test_digest_stays_collaborator_testimony(run_fixture):
    run_dir, scientist = run_fixture
    seat = scientist / "assistant" / "searcher-ep-002"
    _write_json(seat / "manifest.json", {
        "role": "searcher", "collaborator_id": "searcher-ep-002",
    })
    _write_json(seat / "digest.json", {
        "status": "done",
        "report_digest": "virtual calls dominate",
        "evidence": ["profile.txt"],
        "uncertainty": "exact fraction unknown",
    })
    reader = RunReader(RunLayout.discover(run_dir))
    projector = RunProjector(reader.layout.safe_metadata())

    projector.apply(reader.poll())

    report = projector.snapshot()["seats"]["searcher-ep-002"]["activities"][0]
    assert report["kind"] == "report"
    assert report["status"] == "collaborator_testimony"
    assert report["uncertainty"] == "exact fraction unknown"


def test_hostile_text_is_capped_plain_data_and_external_path_hidden(tmp_path):
    payload = "<img src=x onerror=alert(1)>" + "x" * 1000
    text_record = _seat_record(tmp_path, 10, {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": payload}]},
    })
    path_record = _seat_record(tmp_path, 20, {
        "type": "assistant",
        "message": {"content": [{
            "type": "tool_use",
            "id": "read-1",
            "name": "Read",
            "input": {"file_path": "/etc/private-key"},
        }]},
    })

    text_activity = summarize_seat_record(text_record)
    path_activity = summarize_seat_record(path_record)

    assert text_activity["summary"].startswith("<img src=x")
    assert len(text_activity["summary"]) <= 241
    assert "/etc/private-key" not in path_activity["summary"]
    assert path_activity["summary"] == "读取 private-key"
