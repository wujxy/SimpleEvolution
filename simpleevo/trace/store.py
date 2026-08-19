"""Append-only trace store for L1 invocation records."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .envelope import TraceEnvelope, TraceEvent


class TraceStore:
    """Write and read L1 trace files."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.trace_dir = self.run_dir / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, invocation_id: str) -> Path:
        return self.trace_dir / f"{invocation_id}.jsonl"

    def start_invocation(
        self,
        invocation_id: str,
        role: str,
        identity: dict[str, str | None] | None = None,
        output_refs: list[str] | None = None,
    ) -> Path:
        """Write the trace envelope header and return the trace path."""
        path = self._path(invocation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = TraceEnvelope(
            invocation_id=invocation_id,
            role=role,
            identity=identity or {},
            output_refs=output_refs or [],
            timestamp=time.time(),
        )
        path.write_text(
            json.dumps(_serialize(envelope), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def append_event(
        self,
        invocation_id: str,
        event_type: str,
        payload: Any,
        identity: dict[str, str | None] | None = None,
    ) -> None:
        """Append one TraceEvent to an invocation trace."""
        path = self._path(invocation_id)
        event = TraceEvent(
            invocation_id=invocation_id,
            role="",
            event_type=event_type,
            payload=payload,
            identity=identity or {},
            timestamp=time.time(),
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(_serialize(event), ensure_ascii=False) + "\n")
            stream.flush()

    def append_raw_line(
        self,
        invocation_id: str,
        line: str,
        identity: dict[str, str | None] | None = None,
    ) -> None:
        """Append a raw text line as a trace event (fallback for unparseable streams)."""
        self.append_event(invocation_id, "raw_line", line, identity=identity)


def _serialize(obj: Any) -> dict[str, Any]:
    """Serialize a dataclass instance to a plain dict."""
    data: dict[str, Any] = {}
    for key, value in asdict(obj).items():
        if isinstance(value, Path):
            value = str(value)
        data[key] = value
    return data
