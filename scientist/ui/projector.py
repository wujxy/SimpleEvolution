"""Deterministic projection of Scientist run records into UI state."""
from __future__ import annotations

import copy
import re

from .reader import ReaderBatch, SourceRecord


_STEP_RE = re.compile(
    r"\[scientist(?: [0-9:]+)?\] step (\d+)(?:/\d+)?: (.+)$")
_RUN_STATUS = {
    "deliver": "delivered",
    "abstain": "abstained",
    "cut_off": "cut_off",
    "crashed": "crashed",
}
_SEAT_STATUS = {
    "done", "failed", "timeout-salvaged", "crash-salvaged", "unparsed",
}


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

    def snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self._snapshot)

    def _event(
        self,
        event_id: str,
        kind: str,
        summary: str,
        *,
        occurred_at: object = None,
        detail_ref: str | None = None,
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
        detail = f"detail:{record.id}"
        changed = False
        if "resumed from wire.jsonl" in line:
            self._resume_pending = True
            changed |= self._event(
                record.id, "resumed", line, detail_ref=detail)
            self._snapshot["run"]["current_activity"] = "resumed"
        elif "model failure:" in line:
            changed |= self._event(
                record.id, "model_failure", line, detail_ref=detail)
            self._snapshot["run"]["current_activity"] = "model failure"
        elif "infra crash" in line:
            changed |= self._event(
                record.id, "attempt_crashed", line, detail_ref=detail)
        elif match:
            step, activity = int(match.group(1)), match.group(2).strip()
            self._ensure_attempt(step)
            self._snapshot["run"]["current_activity"] = activity
            changed |= self._event(
                record.id, "scientist_step",
                f"Step {step}: {activity}", detail_ref=detail)
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
            "report": None,
            "activities": [],
        })

    def _record(self, record: SourceRecord) -> list[dict[str, object]]:
        source = record.source
        detail = f"detail:{record.id}"
        deltas: list[dict[str, object]] = []
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
                occurred_at=seat["started"], detail_ref=detail)
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
            self._event(
                record.id, "seat_finished",
                f"{seat['role']} {seat_id}: {seat['formal_status']}",
                occurred_at=seat["finished_at"], detail_ref=detail)
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
                occurred_at=value.get("finished_at"), detail_ref=detail)
            deltas.append({"type": "run_updated", "data": {}})
        elif source.startswith("conclusion-history:"):
            value = record.value if isinstance(record.value, dict) else {}
            self._event(
                record.id, "attempt_crashed",
                f"Historical attempt: {value.get('outcome', 'unknown')}",
                occurred_at=value.get("finished_at"), detail_ref=detail)
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
