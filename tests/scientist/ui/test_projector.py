from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.ui.projector import RunProjector
from scientist.ui.reader import RunLayout, RunReader


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


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
