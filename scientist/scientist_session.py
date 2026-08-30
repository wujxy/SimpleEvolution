"""The run's session directory: identity plus the wire log.

Two files, nothing else:

  - ``meta.json``  — stable identity: scientist_id, prompt_version,
                     episode_id.
  - ``wire.jsonl`` — the exact wire messages in arrival order, one JSON
                     object per line, flushed per line. THE single source
                     of truth for the conversation: a resume rebuilds
                     from here and nowhere else.

An older sibling, ``session.jsonl``, was a human-readable derivation
maintained alongside the wire; it drifted every time the wire shape
gained a field (seat-delegation arguments were lost to it, then
reasoning_content) and was retired together with the derivation
machinery. Reading the run means reading the wire.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScientistSession:
    """One run's session: identity on disk, conversation on the wire."""

    session_dir: Path
    scientist_id: str
    meta: dict = field(default_factory=dict)

    @classmethod
    def load_or_create(
        cls,
        session_dir: Path,
        *,
        prompt_version: str,
        episode_id: str | None = None,
    ) -> "ScientistSession":
        """Load the session in ``session_dir``, or stamp a fresh identity."""
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        meta_path = session_dir / "meta.json"
        meta: dict = {}
        if meta_path.exists():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded
            except (OSError, json.JSONDecodeError):
                meta = {}
        if not str(meta.get("scientist_id") or "").strip():
            meta = {
                "scientist_id": uuid.uuid4().hex,
                "prompt_version": prompt_version,
                "episode_id": episode_id,
            }
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        elif episode_id is not None and meta.get("episode_id") != episode_id:
            meta["episode_id"] = episode_id
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return cls(
            session_dir=session_dir,
            scientist_id=str(meta.get("scientist_id")),
            meta=meta,
        )


    @property
    def wire_path(self) -> Path:
        return self.session_dir / "wire.jsonl"

    def append_wire(self, message: dict) -> None:
        """Append one exact wire message (assistant turn with tool_calls
        and reasoning, tool result, user notice). Flush per line: a crash
        mid-run leaves at most one torn line, which load tolerates."""
        with self.wire_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(message, ensure_ascii=False) + "\n")

    def load_wire_messages(self) -> list[dict]:
        """Rebuild the conversation from the wire log. A torn trailing
        line (crash mid-write) is skipped; anything without a role is
        skipped — the log is only ever appended by append_wire."""
        messages: list[dict] = []
        if not self.wire_path.exists():
            return messages
        for line in self.wire_path.read_text(
                encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("role"):
                messages.append(message)
        return _complete_dangling_calls(messages)
def _complete_dangling_calls(messages: list[dict]) -> list[dict]:
    """A hard kill can drop the tool results of an in-flight call: the
    assistant message is already on the wire, the result never arrived,
    and whatever is appended after the gap (resume notices, budget
    notes) leaves the pair permanently open. Sent to the model as-is,
    that conversation is rejected — tool_calls must be followed by tool
    messages. Complete the view: synthesize an interrupted marker for
    every unanswered call. The wire file itself is never rewritten."""
    repaired: list[dict] = []
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            if pending:
                repaired.extend(_interrupted_results(pending))
                pending = set()
            repaired.append(message)
            pending = {t.get("id") for t in message["tool_calls"]} - {None}
            continue
        if role == "tool" and pending:
            pending.discard(message.get("tool_call_id"))
            repaired.append(message)
            continue
        if pending:
            repaired.extend(_interrupted_results(pending))
            pending = set()
        repaired.append(message)
    if pending:
        repaired.extend(_interrupted_results(pending))
    return repaired


def _interrupted_results(pending: set[str]) -> list[dict]:
    return [
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": '{"ok": false, "error": "this call was interrupted '
                       'by a run restart before any result was recorded"}',
        }
        for call_id in sorted(pending)
    ]
