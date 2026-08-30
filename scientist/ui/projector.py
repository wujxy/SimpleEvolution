"""Deterministic projection of Scientist run records into UI state."""
from __future__ import annotations

import copy
import re
import shlex
import time
from datetime import datetime
from pathlib import Path

from .reader import ReaderBatch, SourceRecord


_STEP_RE = re.compile(
    r"\[scientist(?: [0-9:]+)?\] step (\d+)(?:/\d+)?: (.+)$")
_TIME_RE = re.compile(r"\[scientist (\d{2}):(\d{2}):(\d{2})\]")
_RUN_STATUS = {
    "deliver": "delivered",
    "abstain": "abstained",
    "cut_off": "cut_off",
    "crashed": "crashed",
}
_SEAT_STATUS = {
    "done", "failed", "timeout-salvaged", "crash-salvaged", "unparsed",
}
_TOOL_LABELS = {
    "Read": "读取",
    "Grep": "搜索",
    "Glob": "查找文件",
    "Edit": "修改",
    "Write": "写入",
    "Bash": "运行",
    "WebSearch": "检索网页",
    "WebFetch": "读取网页",
    "Task": "派出子任务",
}
_KNOWN_COMMANDS = {
    "benchmark.sh", "quick_bench.sh", "sl_eval_v100.sh",
    "pytest", "cmake", "make",
}


def _cap(text: object, limit: int = 240) -> str:
    shown = str(text or "").strip().replace("\x00", "")
    return shown if len(shown) <= limit else shown[:limit] + "…"


def _safe_path(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown path"
    if text.startswith("/work/"):
        return text[len("/work/"):]
    if Path(text).is_absolute():
        return Path(text).name
    return text


def _bash_summary(command: object) -> str:
    first = str(command or "").strip().splitlines()[0] if command else ""
    try:
        tokens = shlex.split(first)
    except ValueError:
        tokens = first.split()
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name in _KNOWN_COMMANDS:
            args = " ".join(tokens[index + 1:index + 5])
            return f"运行 {name}" + (f" {args}" if args else "")
    return "运行 " + _cap(first or "command", 160)


def _tool_summary(name: str, inputs: dict) -> str:
    if name == "Bash":
        return _bash_summary(inputs.get("command"))
    label = _TOOL_LABELS.get(name, name or "工具")
    if name in {"Read", "Edit", "Write"}:
        path = inputs.get("file_path") or inputs.get("path")
        return f"{label} {_safe_path(path)}"
    if name in {"Grep", "Glob"}:
        pattern = _cap(inputs.get("pattern") or inputs.get("query"), 100)
        root = _safe_path(inputs.get("path") or ".")
        return f"{label} {pattern} · {root}"
    if name in {"WebSearch", "WebFetch"}:
        return f"{label} {_cap(inputs.get('query') or inputs.get('url'), 160)}"
    return f"{label} {_cap(inputs.get('description') or '', 160)}".strip()


def _day_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)):
        shown = time.localtime(float(value))
        return float(shown.tm_hour * 3600 + shown.tm_min * 60 + shown.tm_sec)
    if isinstance(value, str):
        try:
            shown = datetime.fromisoformat(value)
        except ValueError:
            return None
        return float(shown.hour * 3600 + shown.minute * 60 + shown.second)
    return None


def summarize_seat_record(
    record: SourceRecord,
) -> dict[str, object] | None:
    """Return one conservative activity from a Claude stream event."""
    value = record.value
    if not isinstance(value, dict):
        return None
    event_type = value.get("type")
    detail = f"detail:{record.id}"
    if event_type == "assistant":
        content = (value.get("message") or {}).get("content") or []
        for chunk in content:
            if not isinstance(chunk, dict):
                continue
            if chunk.get("type") == "tool_use":
                tool_id = str(chunk.get("id") or record.id)
                name = str(chunk.get("name") or "Tool")
                inputs = (
                    chunk.get("input")
                    if isinstance(chunk.get("input"), dict) else {}
                )
                return {
                    "id": f"activity:{record.id}:{tool_id}",
                    "kind": "tool",
                    "summary": _tool_summary(name, inputs),
                    "status": "running",
                    "tool_use_id": tool_id,
                    "detail_refs": [detail],
                    "sequence": record.offset,
                }
            if chunk.get("type") == "text" and str(
                    chunk.get("text") or "").strip():
                return {
                    "id": f"activity:{record.id}",
                    "kind": "intent",
                    "summary": _cap(chunk.get("text")),
                    "status": "stated",
                    "tool_use_id": None,
                    "detail_refs": [detail],
                    "sequence": record.offset,
                }
    if event_type == "tool_progress":
        tool_id = str(value.get("tool_use_id") or "")
        return {
            "id": f"activity:{record.id}",
            "kind": "tool",
            "summary": f"{value.get('tool_name') or '工具'} 仍在运行",
            "status": "running",
            "tool_use_id": tool_id or None,
            "detail_refs": [detail],
            "sequence": record.offset,
        }
    if event_type == "user":
        content = (value.get("message") or {}).get("content") or []
        for chunk in content:
            if (isinstance(chunk, dict)
                    and chunk.get("type") == "tool_result"):
                tool_id = str(chunk.get("tool_use_id") or "")
                return {
                    "id": f"activity:{record.id}",
                    "kind": "tool",
                    "summary": "工具执行完成",
                    "status": (
                        "failed" if chunk.get("is_error") else "succeeded"),
                    "tool_use_id": tool_id or None,
                    "detail_refs": [detail],
                    "sequence": record.offset,
                }
    return None


class RunProjector:
    """Replay file facts into one serializable snapshot."""

    def __init__(self, metadata: dict[str, object]):
        self._snapshot = {
            "run": {
                "metadata": copy.deepcopy(metadata),
                "formal_status": "unconcluded",
                "outcome": None,
                "last_observed_at": None,
                "current_activity": "Unavailable",
                "current_judgment": None,
            },
            "attempts": [],
            "timeline": [],
            "seats": {},
            "warnings": [],
            "usage": {"calls": 0, "total_tokens": 0},
            "indexing": True,
        }
        self._event_ids: set[str] = set()
        self._warning_keys: set[tuple[str, str]] = set()
        self._resume_pending = False
        self._activity_tools: dict[tuple[str, str], dict[str, object]] = {}

    def snapshot(self) -> dict[str, object]:
        result = copy.deepcopy(self._snapshot)
        timeline = result["timeline"]
        timeline.sort(key=lambda event: (
            event.get("_sort_key") is None,
            event.get("_sort_key") if event.get("_sort_key") is not None
            else event["sequence"],
            event["sequence"],
        ))
        for sequence, event in enumerate(timeline, 1):
            event["sequence"] = sequence
            event.pop("_sort_key", None)
        return result

    def _event(
        self,
        event_id: str,
        kind: str,
        summary: str,
        *,
        occurred_at: object = None,
        detail_ref: str | None = None,
        sort_key: float | None = None,
    ) -> bool:
        if event_id in self._event_ids:
            return False
        self._event_ids.add(event_id)
        event = {
            "id": event_id,
            "kind": kind,
            "summary": summary,
            "occurred_at": occurred_at,
            "sequence": len(self._snapshot["timeline"]) + 1,
            "detail_refs": [detail_ref] if detail_ref else [],
            "_sort_key": sort_key,
        }
        self._snapshot["timeline"].append(event)
        return True

    def _ensure_attempt(self, step: int) -> None:
        attempts = self._snapshot["attempts"]
        if not attempts or self._resume_pending:
            attempts.append({
                "number": len(attempts) + 1,
                "first_step": step,
                "last_step": step,
            })
            self._resume_pending = False
        else:
            attempts[-1]["last_step"] = step

    def _run_log(self, record: SourceRecord) -> bool:
        line = str(record.value)
        match = _STEP_RE.search(line)
        time_match = _TIME_RE.search(line)
        occurred_at = None
        sort_key = None
        if time_match:
            hour, minute, second = (int(item) for item in time_match.groups())
            occurred_at = f"{hour:02d}:{minute:02d}:{second:02d}"
            sort_key = float(hour * 3600 + minute * 60 + second)
        detail = f"detail:{record.id}"
        changed = False
        if "resumed from wire.jsonl" in line:
            self._resume_pending = True
            changed |= self._event(
                record.id, "resumed", line, occurred_at=occurred_at,
                detail_ref=detail, sort_key=sort_key)
            self._snapshot["run"]["current_activity"] = "resumed"
        elif "model failure:" in line:
            changed |= self._event(
                record.id, "model_failure", line, occurred_at=occurred_at,
                detail_ref=detail, sort_key=sort_key)
            self._snapshot["run"]["current_activity"] = "model failure"
        elif "infra crash" in line:
            changed |= self._event(
                record.id, "attempt_crashed", line, occurred_at=occurred_at,
                detail_ref=detail, sort_key=sort_key)
        elif match:
            step, activity = int(match.group(1)), match.group(2).strip()
            self._ensure_attempt(step)
            self._snapshot["run"]["current_activity"] = activity
            changed |= self._event(
                record.id, "scientist_step",
                f"Step {step}: {activity}", occurred_at=occurred_at,
                detail_ref=detail, sort_key=sort_key)
        return changed

    def _seat(self, seat_id: str) -> dict[str, object]:
        seats = self._snapshot["seats"]
        return seats.setdefault(seat_id, {
            "collaborator_id": seat_id,
            "role": "unknown",
            "brief": "",
            "formal_status": "started",
            "delivered": False,
            "started": None,
            "finished_at": None,
            "box_seconds": None,
            "last_activity_at": None,
            "report": None,
            "activities": [],
        })

    def _record(self, record: SourceRecord) -> list[dict[str, object]]:
        source = record.source
        detail = f"detail:{record.id}"
        deltas: list[dict[str, object]] = []
        observed = record.observed_at
        current_observed = self._snapshot["run"]["last_observed_at"]
        if observed is not None and (
                current_observed is None or observed > current_observed):
            self._snapshot["run"]["last_observed_at"] = observed
        if source == "run-log":
            if self._run_log(record):
                deltas.append({"type": "event_added",
                               "data": {"event_id": record.id}})
        elif source.startswith("seat-manifest:"):
            seat_id = source.split(":", 1)[1]
            value = record.value if isinstance(record.value, dict) else {}
            seat = self._seat(seat_id)
            seat.update({
                "role": str(value.get("role") or "unknown"),
                "brief": str(value.get("brief") or ""),
                "started": value.get("started"),
                "box_seconds": value.get("box"),
            })
            self._event(
                record.id, "seat_started",
                f"{seat['role']} {seat_id} started",
                occurred_at=seat["started"], detail_ref=detail,
                sort_key=_day_seconds(seat["started"]))
            deltas.append({"type": "seat_updated",
                           "data": {"collaborator_id": seat_id}})
        elif source.startswith("seat-digest:"):
            seat_id = source.split(":", 1)[1]
            value = record.value if isinstance(record.value, dict) else {}
            seat = self._seat(seat_id)
            status = str(value.get("status") or "failed")
            seat["formal_status"] = (
                status if status in _SEAT_STATUS else "failed")
            seat["finished_at"] = value.get("finished_at")
            seat["report"] = value.get("report_digest")
            seat["activities"].append({
                "id": f"activity:{record.id}",
                "kind": "report",
                "summary": _cap(value.get("report_digest"), 1200),
                "status": "collaborator_testimony",
                "uncertainty": value.get("uncertainty"),
                "evidence": (
                    value.get("evidence")
                    if isinstance(value.get("evidence"), list) else []
                ),
                "detail_refs": [detail],
                "sequence": record.offset,
            })
            self._event(
                record.id, "seat_finished",
                f"{seat['role']} {seat_id}: {seat['formal_status']}",
                occurred_at=seat["finished_at"], detail_ref=detail,
                sort_key=_day_seconds(seat["finished_at"]))
            deltas.append({"type": "seat_updated",
                           "data": {"collaborator_id": seat_id}})
        elif source.startswith("seat-read:"):
            seat_id = source.split(":", 1)[1]
            self._seat(seat_id)["delivered"] = True
            self._event(
                record.id, "seat_delivered",
                f"{seat_id} report delivered to Scientist")
            deltas.append({"type": "seat_updated",
                           "data": {"collaborator_id": seat_id}})
        elif source.startswith("seat:"):
            seat_id = source.split(":", 1)[1]
            seat = self._seat(seat_id)
            if observed is not None:
                seat["last_activity_at"] = observed
            activity = summarize_seat_record(record)
            if activity is None:
                return deltas
            tool_id = activity.get("tool_use_id")
            key = (seat_id, str(tool_id)) if tool_id else None
            existing = self._activity_tools.get(key) if key else None
            if existing is None:
                seat["activities"].append(activity)
                if key:
                    self._activity_tools[key] = activity
            else:
                for ref in activity["detail_refs"]:
                    if ref not in existing["detail_refs"]:
                        existing["detail_refs"].append(ref)
                if activity["status"] != "running":
                    existing["status"] = activity["status"]
            deltas.append({"type": "seat_updated",
                           "data": {"collaborator_id": seat_id}})
        elif source == "research-state" and isinstance(record.value, dict):
            value = dict(record.value)
            judgment = value.get("judgment") or value.get("working_model")
            if judgment:
                value["judgment"] = judgment
                self._snapshot["run"]["current_judgment"] = value
                self._event(
                    record.id, "judgment_revised",
                    str(judgment), detail_ref=detail)
                deltas.append({"type": "run_updated", "data": {}})
        elif source == "usage" and isinstance(record.value, dict):
            self._snapshot["usage"]["calls"] += 1
            total = record.value.get("total_tokens")
            if isinstance(total, int):
                self._snapshot["usage"]["total_tokens"] += total
            deltas.append({"type": "run_updated", "data": {}})
        elif source == "conclusion:current":
            value = record.value if isinstance(record.value, dict) else {}
            outcome = str(value.get("outcome") or "")
            self._snapshot["run"]["outcome"] = outcome or None
            self._snapshot["run"]["formal_status"] = _RUN_STATUS.get(
                outcome, "unconcluded")
            self._snapshot["run"]["current_activity"] = "concluded"
            self._event(
                record.id, "conclusion", f"Run concluded: {outcome}",
                occurred_at=value.get("finished_at"), detail_ref=detail,
                sort_key=_day_seconds(value.get("finished_at")))
            deltas.append({"type": "run_updated", "data": {}})
        elif source.startswith("conclusion-history:"):
            value = record.value if isinstance(record.value, dict) else {}
            self._event(
                record.id, "attempt_crashed",
                f"Historical attempt: {value.get('outcome', 'unknown')}",
                occurred_at=value.get("finished_at"), detail_ref=detail,
                sort_key=_day_seconds(value.get("finished_at")))
            deltas.append({"type": "event_added",
                           "data": {"event_id": record.id}})
        elif source == "wire" and isinstance(record.value, dict):
            role = str(record.value.get("role") or "wire")
            tool_names = [
                str((item.get("function") or {}).get("name") or "tool")
                for item in record.value.get("tool_calls") or []
                if isinstance(item, dict)
            ]
            summary = (
                f"Scientist called {', '.join(tool_names)}"
                if tool_names else f"Scientist wire message: {role}"
            )
            self._event(
                record.id, "wire", summary, detail_ref=detail)
            deltas.append({"type": "event_added",
                           "data": {"event_id": record.id}})
        return deltas

    def apply(self, batch: ReaderBatch) -> list[dict[str, object]]:
        deltas: list[dict[str, object]] = []
        for warning in batch.warnings:
            key = (warning.source, warning.message)
            if key in self._warning_keys:
                continue
            self._warning_keys.add(key)
            item = {"source": warning.source, "message": warning.message}
            self._snapshot["warnings"].append(item)
            deltas.append({"type": "observer_warning", "data": item})
        for record in batch.records:
            deltas.extend(self._record(record))
        indexing = not batch.initial_index_complete
        if self._snapshot["indexing"] != indexing:
            self._snapshot["indexing"] = indexing
            deltas.append({"type": "run_updated", "data": {}})
        return deltas
