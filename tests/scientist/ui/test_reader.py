from __future__ import annotations

import json
from pathlib import Path

import pytest

from scientist.ui.reader import DetailIndex, LineCursor, RunLayout


def test_discover_requires_world_scientist(tmp_path: Path):
    with pytest.raises(ValueError, match=r"world/.scientist"):
        RunLayout.discover(tmp_path)


def test_safe_metadata_is_an_output_whitelist(run_fixture):
    run_dir, _ = run_fixture
    metadata = RunLayout.discover(run_dir).safe_metadata()
    assert metadata == {
        "goal": "make reconstruction faster",
        "episode_id": "ep-7",
        "budget": {"steps": 3000, "wall_seconds": 604800},
    }
    rendered = repr(metadata)
    assert "TOP-SECRET" not in rendered
    assert "SECRET-TOKEN" not in rendered
    assert "secret-host" not in rendered


def test_source_path_rejects_escape(run_fixture):
    run_dir, _ = run_fixture
    layout = RunLayout.discover(run_dir)
    with pytest.raises(ValueError, match="outside selected run"):
        layout.source_path("../private-key")


def test_cursor_waits_for_torn_json_then_emits_stable_offset(tmp_path):
    path = tmp_path / "wire.jsonl"
    path.write_bytes(b'{"content":"one"}\n{"content":"two"')
    cursor = LineCursor(path, source="wire", json_lines=True)

    first = cursor.poll()

    assert [row.offset for row in first.records] == [0]
    assert first.records[0].value["content"] == "one"
    assert first.warnings == []
    with path.open("ab") as handle:
        handle.write(b"}\n")

    second = cursor.poll()

    assert second.records[0].value["content"] == "two"
    assert second.records[0].id == f"wire:{second.records[0].offset}"


def test_cursor_warns_and_rebuilds_after_truncation(tmp_path):
    path = tmp_path / "wire.jsonl"
    path.write_text('{"n":1}\n{"n":2}\n', encoding="utf-8")
    cursor = LineCursor(path, source="wire", json_lines=True)
    assert len(cursor.poll().records) == 2

    path.write_text('{"n":3}\n', encoding="utf-8")
    batch = cursor.poll()

    assert batch.reset is True
    assert [row.value for row in batch.records] == [{"n": 3}]
    assert "truncated or replaced" in batch.warnings[0].message


def test_detail_index_reads_only_registered_slice(tmp_path):
    path = tmp_path / "raw.txt"
    path.write_bytes(b'{"type":"assistant","text":"safe"}\n')
    record = LineCursor(
        path, source="seat:executor-1", json_lines=True,
    ).poll().records[0]
    index = DetailIndex()

    detail_id = index.register(record)

    assert index.read(detail_id) == {
        "detail_id": detail_id,
        "source": "seat:executor-1",
        "content": {"type": "assistant", "text": "safe"},
        "truncated": False,
    }
    with pytest.raises(KeyError):
        index.read("detail:../../private-key")


def test_detail_index_caps_large_records(tmp_path):
    path = tmp_path / "raw.txt"
    path.write_text(json.dumps({"payload": "x" * 100}) + "\n",
                    encoding="utf-8")
    record = LineCursor(
        path, source="seat:x", json_lines=True,
    ).poll().records[0]
    index = DetailIndex()
    detail_id = index.register(record)

    detail = index.read(detail_id, max_bytes=20)

    assert detail["truncated"] is True
    assert isinstance(detail["content"], str)
    assert len(detail["content"].encode()) <= 20


def test_cursor_reads_large_history_in_bounded_chunks(tmp_path):
    path = tmp_path / "raw.txt"
    line = json.dumps({"type": "system", "payload": "x" * 2000}) + "\n"
    path.write_text(line * 3000, encoding="utf-8")
    cursor = LineCursor(
        path, source="seat:x", json_lines=True,
        max_read_bytes=1024 * 1024,
    )

    seen = []
    batch_sizes = []
    while cursor.offset < path.stat().st_size or cursor.pending:
        batch = cursor.poll()
        seen.extend(batch.records)
        batch_sizes.append(cursor.last_read_bytes)

    assert len(seen) == 3000
    assert cursor.pending == b""
    assert all(size <= 1024 * 1024 for size in batch_sizes)
    assert cursor.poll().records == []
