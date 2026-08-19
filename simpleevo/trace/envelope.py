"""Unified L1 trace envelope.

Every proposer or experiment invocation gets a trace file under
``run_dir/traces/<invocation_id>.jsonl``.  Each line is a
``TraceEvent`` that tags the invocation's role, identity refs, and
payload (a stream-json event, a log line, or an artifact reference).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceEvent:
    """One append-only record in an invocation trace."""

    invocation_id: str
    role: str
    event_type: str
    payload: Any
    identity: dict[str, str | None] = field(default_factory=dict)
    timestamp: float | None = None


@dataclass(frozen=True)
class TraceEnvelope:
    """Header written as the first line of a trace file."""

    invocation_id: str
    role: str
    identity: dict[str, str | None] = field(default_factory=dict)
    output_refs: list[str] = field(default_factory=list)
    timestamp: float | None = None
