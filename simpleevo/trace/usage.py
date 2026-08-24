"""Append-only LLM token-usage recorder.

Every proposer/experiment model call reports its token usage once; the recorder
appends one line per invocation to ``run_dir/telemetry/usage.jsonl``. The
reporting layer replays these lines to compute budget (USD) series. This is the
single aggregation point for token accounting — without it, a run's LLM spend
is unrecoverable.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def extract_usage(usage: Any) -> dict[str, int] | None:
    """Normalise a model usage object to a flat token dict.

    Accepts Claude (``input_tokens`` / ``output_tokens`` / ``cache_*``) and
    OpenAI-compatible (``prompt_tokens`` / ``completion_tokens``) shapes.
    Returns ``None`` when nothing countable is present.
    """
    if usage is None:
        return None
    data: Any = usage if isinstance(usage, dict) else None
    if data is None and hasattr(usage, "model_dump"):
        data = usage.model_dump()
    if not isinstance(data, dict):
        return None

    def first(*keys: str) -> int:
        for key in keys:
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return 0

    input_tokens = first("input_tokens", "prompt_tokens")
    output_tokens = first("output_tokens", "completion_tokens")
    cache_read = first("cache_read_input_tokens")
    cache_creation = first("cache_creation_input_tokens")

    # OpenAI-compatible cache accounting.  Anthropic-shaped usage reports
    # ``input_tokens`` EXCLUDING cache reads (cache_read_input_tokens is a
    # separate counter), so the totals above are already disjoint.  OpenAI
    # shaped usage (DeepSeek's chat-completions endpoint) reports
    # ``prompt_tokens`` INCLUDING cached tokens, plus the cached portion in
    # ``prompt_cache_hit_tokens`` (DeepSeek-specific) or the standard nested
    # ``prompt_tokens_details.cached_tokens``.  Without mapping those, the
    # proposer's cache hits are charged at the full fresh-input price and the
    # budget curve overstates the researcher's cost.
    if not cache_read:
        cached = first("prompt_cache_hit_tokens")
        if not cached:
            details = data.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached = int(details.get("cached_tokens", 0) or 0)
        if cached:
            cache_read = cached
            input_tokens = max(0, input_tokens - cached)

    if not any((input_tokens, output_tokens, cache_read, cache_creation)):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


class UsageRecorder:
    """Append one usage record per model call to usage.jsonl."""

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "telemetry" / "usage.jsonl"

    def record(self, role: str, usage: Any, *, work_id: str | None = None) -> None:
        """Append one usage record; ``work_id`` attributes it to a lease.

        A record without ``work_id`` (all pre-complete-research records)
        is only run-attributable — lease-level budgets skip it, run-level
        caps are unaffected.
        """
        tokens = extract_usage(usage)
        if tokens is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"role": role, "timestamp": time.time(), **tokens}
        if work_id:
            record["work_id"] = work_id
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
