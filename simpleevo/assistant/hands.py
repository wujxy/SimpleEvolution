"""The seat's two hands on its claude assistant: consult and work.

科学家完整研究制 §2.2 — the interface is a permission boundary, not a
capability menu:

- ``consult(question, context, read)`` — the belief channel.  It never
  touches the world: the sandbox carries no write tools and (when a world
  is attached at all) mounts it read-only.  Whatever comes back is a
  distilled unsigned opinion the seat may adopt or discard; the judgment
  stays with the seat.
- ``work(instruction, mode, budget)`` — the lab channel.  A claude session
  works inside the seat's laboratory (the seat's own shell shares the same
  world); the harness snapshots the editable paths into a side-chain
  commit and returns a distillation only.

The distillation contract is mechanical, not aspirational: the raw
transcript is written to ``run_dir/assistant/<call_id>/`` and NEVER
returned to the researcher's context; the digest is hard-capped in words.
Every call lands in the ``assistant_calls`` ledger (question digest,
adoption, tokens, by lens — the oracle-homogenization measurement face)
and its usage in the run's token ledger.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from simpleevo.contracts import (
    ExecutionSandbox, MountMode, MountSpec, SandboxSpec, WorkspaceSpec,
)

from .agent import Agent
from .lab import Laboratory

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# The consult channel carries no world-writing tools even when a world is
# mounted read-only: the permission boundary is expressed twice (mounts
# AND tools) so neither layer's failure silently opens it.
_CONSULT_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch,Task"
_WORK_TOOLS = "Read,Grep,Glob,Edit,Write,Bash,WebSearch,WebFetch"

_CONSULT_PROMPT = """You are the research assistant of a scientist who owns this investigation.
They are asking you a question; you are not the researcher of record.

Question:
{question}

Context they gave you:
{context}
{world_note}
Answer with a distilled judgment — at most {cap} words. Do the searching
and the weighing yourself (you may use web search and subagents); return
only the conclusion and what it stands on. End your reply with exactly
one fenced JSON block:

```json
{{"answer_digest": "<= {cap} words, your distilled answer>",
  "sources": ["what you based it on, one line each"]}}
```

If the honest answer is "unknown / no evidence", say exactly that.
"""

_WORK_PROMPT = """You are the hands of a scientist who owns this investigation.
Work INSIDE this world at /work (the whole tree is writable; build and
self-measure in place — the harness owns git, do not commit).

Your scientist's instruction:
{instruction}
{measure_note}
When done, reply with a distilled report — the diff summary and your
self-report TOGETHER must stay under {cap} words (the raw detail stays in
the world; your scientist can read the code). End with exactly one fenced
JSON block:

```json
{{"diff_summary": "what changed in the world",
  "self_report_digest": "what you did, why, what still needs verifying",
  "metrics": {{"self_measured": "key numbers from your own runs"}}}}
```

If the instruction cannot be completed, say so plainly in
self_report_digest and leave the world in a state that compiles.
"""


def _cap_words(text: str, cap: int) -> tuple[str, bool]:
    words = text.split()
    if len(words) <= cap:
        return text, False
    return " ".join(words[:cap]) + " …[truncated at cap]", True


def _parse_tail(text: str) -> dict[str, Any] | None:
    for raw in reversed(_JSON_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


@dataclass
class HandTally:
    """Per-attempt call budgets (the lease-level caps live with the store)."""

    consult_calls: int = 0
    work_calls: int = 0
    max_consult_calls: int | None = None
    max_work_calls: int | None = None

    def allow(self, kind: str) -> tuple[bool, str]:
        if kind == "consult":
            if (self.max_consult_calls is not None
                    and self.consult_calls >= self.max_consult_calls):
                return False, "consult budget exhausted for this attempt"
        elif kind == "work":
            if (self.max_work_calls is not None
                    and self.work_calls >= self.max_work_calls):
                return False, "work budget exhausted for this attempt"
        return True, ""


@dataclass
class AssistantHands:
    """One lease's consult/work channels over the claude CLI."""

    run_dir: Path
    db_path: Path
    lease_id: str
    episode_id: str
    node_id: str
    node_sha: str
    lens: str | None
    lab: Laboratory
    runtime_image: Path
    executor_cfg: dict[str, Any]
    editable_paths: tuple[str, ...] = ()
    read_only_binds: tuple[Path, ...] = ()
    distill_word_cap: int = 300
    consult_timeout_seconds: int = 900
    work_default_minutes: int = 30
    tally: HandTally = field(default_factory=HandTally)
    agent_factory: Callable[..., Agent] | None = None
    usage_observer: Callable[[Any, str], None] | None = None
    trace_store: Any = None
    attempt_id: str | None = None
    _counter: int = 0

    # -- plumbing ----------------------------------------------------------

    def _next_call_id(self, kind: str) -> str:
        self._counter += 1
        return f"{kind}-{self.episode_id}-{self._counter:03d}"

    def _raw_dir(self, call_id: str) -> Path:
        d = self.run_dir / "assistant" / call_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sandbox_with(
        self, mounts: tuple[MountSpec, ...],
    ) -> ExecutionSandbox:
        from simpleevo.runtime import ApptainerSandbox, executor_environment

        sandbox = ApptainerSandbox(userns=True)
        return sandbox.bind(SandboxSpec(
            image=self.runtime_image,
            environment=executor_environment(
                base_url=self.executor_cfg.get("base_url"),
                max_output_tokens=64000,
                api_key=self.executor_cfg.get("api_key"),
            ),
            network=True,
        ), mounts)

    def _agent(
        self, call_id: str, *, world: ExecutionSandbox,
        allowed_tools: str, timeout: int, label: str,
    ) -> Agent:
        extra: list[str] = []
        if self.executor_cfg.get("effort"):
            extra = ["--effort", str(self.executor_cfg["effort"])]
        factory = self.agent_factory or Agent
        return factory(
            world=world,
            allowed_tools=allowed_tools,
            timeout_seconds=timeout,
            model=self.executor_cfg.get("model"),
            extra_args=extra,
            usage_observer=(
                lambda usage: self._record_usage(usage, call_id)),
            trace_store=self.trace_store,
            invocation_id=f"assistant-{call_id}",
            role="assistant",
            identity={
                "call_id": call_id, "kind": label,
                "episode_id": self.episode_id,
                "node_id": self.node_id, "lease_id": self.lease_id,
                "attempt_id": self.attempt_id,
            },
        )

    def _record_usage(self, usage: Any, call_id: str) -> None:
        if self.usage_observer is not None:
            self.usage_observer(usage, call_id)

    def _ledger(self, call_id: str, kind: str, digest: str,
                world_sha: str | None) -> None:
        from simpleevo.db.lease_writer import record_assistant_call

        record_assistant_call(
            self.db_path,
            call_id=call_id,
            episode_id=self.episode_id,
            lease_id=self.lease_id,
            lens=self.lens,
            kind=kind,
            question_digest=digest[:400],
            adopted=None,
            usage=None,
            world_sha=world_sha,
        )

    def _open_resource_row(self, call_id: str, opened_at: float) -> None:
        from simpleevo.db.lease_writer import record_resource_row

        record_resource_row(
            self.db_path,
            ref_id=call_id,
            kind="work",
            allocation_id=self.lease_id,
            opened_at=opened_at,
        )

    def _close_resource_row(self, call_id: str) -> None:
        from simpleevo.db.lease_writer import close_resource_row

        close_resource_row(self.db_path, ref_id=call_id)

    def _persist_raw(
        self, call_id: str, prompt: str, raw: str, digest: dict,
    ) -> None:
        d = self._raw_dir(call_id)
        (d / "prompt.txt").write_text(prompt, encoding="utf-8")
        (d / "raw.txt").write_text(raw or "", encoding="utf-8")
        (d / "digest.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_binds(self) -> list[MountSpec]:
        return [
            MountSpec(Path(p), PurePosixPath(p), MountMode.READ_ONLY)
            for p in self.read_only_binds
        ]

    # -- consult (belief channel: never writes the world) ------------------

    def consult(
        self,
        question: str,
        context: str = "",
        read: str = "none",
    ) -> dict[str, Any]:
        ok, why = self.tally.allow("consult")
        if not ok:
            return {"status": "refused", "reason": why}
        if read not in {"none", "node", "lab"}:
            return {
                "status": "invalid",
                "reason": f"read must be none|node|lab, got {read!r}",
            }
        call_id = self._next_call_id("consult")

        mounts: list[MountSpec] = []
        world_note = ""
        world_cwd = PurePosixPath("/")
        if read == "node":
            mounts.append(MountSpec(
                self._node_world_path(), PurePosixPath("/work"),
                MountMode.READ_ONLY))
            world_note = (
                "\nA pristine read-only copy of the world under study is "
                "mounted at /work.")
            world_cwd = PurePosixPath("/work")
        elif read == "lab":
            mounts.append(MountSpec(
                self.lab.path, PurePosixPath("/work"), MountMode.READ_ONLY))
            world_note = (
                "\nThe scientist's current laboratory (work in progress) "
                "is mounted read-only at /work.")
            world_cwd = PurePosixPath("/work")
        mounts.extend(self._read_binds())

        prompt = _CONSULT_PROMPT.format(
            question=question, context=context or "(none)",
            world_note=world_note, cap=self.distill_word_cap,
        )
        agent = self._agent(
            call_id,
            world=self._sandbox_with(tuple(mounts)),
            allowed_tools=_CONSULT_TOOLS,
            timeout=self.consult_timeout_seconds, label="consult",
        )
        raw = agent.run_text(
            prompt, cwd=self.lab.path, label=f"consult {call_id}",
            world_cwd=world_cwd,
        )
        tail = _parse_tail(raw) or {}
        digest = str(tail.get("answer_digest") or raw or "").strip()
        digest, truncated = _cap_words(digest, self.distill_word_cap)
        sources = tail.get("sources") or []
        self._persist_raw(call_id, prompt, raw, {
            "answer_digest": digest, "sources": sources,
            "truncated": truncated,
        })
        self.tally.consult_calls += 1
        self._ledger(call_id, "consult", f"{question} -> {digest}", None)
        return {
            "call_id": call_id,
            # The belief-channel stamp: this is the assistant's opinion,
            # not verified fact — adopting it is the seat's judgment.
            "channel": "belief",
            "answer_digest": digest,
            "sources": sources,
            "truncated": truncated,
        }

    # -- work (the lab channel) --------------------------------------------

    def work(
        self,
        instruction: str,
        mode: str = "continue",
        budget_minutes: float | None = None,
    ) -> dict[str, Any]:
        ok, why = self.tally.allow("work")
        if not ok:
            return {"status": "refused", "reason": why}
        if mode not in {"continue", "fresh"}:
            return {
                "status": "invalid",
                "reason": f"mode must be continue|fresh, got {mode!r}",
            }
        call_id = self._next_call_id("work")
        minutes = float(budget_minutes or self.work_default_minutes)
        timeout = max(60, int(minutes * 60))
        self._open_resource_row(call_id, time.time())

        lab_ws = self.lab.main()
        measure_note = (
            "\nThis task has a benchmark: self-measure in the world (the "
            "eval scripts are there) and report the numbers you measured."
            if self.editable_paths else ""
        )
        prompt = _WORK_PROMPT.format(
            instruction=instruction, measure_note=measure_note,
            cap=self.distill_word_cap,
        )

        side_id = None
        work_dir = lab_ws.path
        if mode == "fresh":
            side_id = f"lab-side-{self.episode_id}-{call_id}"
            side_ws = self.lab.provider.create(
                WorkspaceSpec(side_id, self.node_sha))
            work_dir = side_ws.path

        try:
            mounts: list[MountSpec] = [MountSpec(
                work_dir, PurePosixPath("/work"), MountMode.READ_WRITE)]
            mounts.append(MountSpec(
                self.lab.provider.repo, PurePosixPath("/repo"),
                MountMode.READ_ONLY))
            mounts.extend(self._read_binds())
            agent = self._agent(
                call_id,
                world=self._sandbox_with(tuple(mounts)),
                allowed_tools=_WORK_TOOLS,
                timeout=timeout, label="work",
            )
            raw = agent.run_text(prompt, cwd=work_dir, label=f"work {call_id}")
        finally:
            if side_id is not None:
                self.lab.provider._remove_path(work_dir)

        tail = _parse_tail(raw) or {}
        diff_summary, d_trunc = _cap_words(
            str(tail.get("diff_summary") or ""), self.distill_word_cap)
        self_report, s_trunc = _cap_words(
            str(tail.get("self_report_digest") or raw or ""),
            self.distill_word_cap)
        metrics = tail.get("metrics") if isinstance(
            tail.get("metrics"), dict) else {}

        world_sha = None
        if mode == "continue":
            world_sha = self.lab.snapshot(call_id)
        self._persist_raw(call_id, prompt, raw, {
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics, "world_sha": world_sha,
        })
        self.tally.work_calls += 1
        self._ledger(
            call_id, "work", f"{instruction} -> {self_report}", world_sha,
        )
        self._close_resource_row(call_id)
        return {
            "call_id": call_id,
            "status": "done" if tail else "unparsed",
            "mode": mode,
            "world_sha": world_sha,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics,
            "truncated": bool(d_trunc or s_trunc),
            # The harness snapshot and the adjudication gate are the truth
            # channel; these numbers are the assistant's own report.
            "channel": "belief",
        }

    # -- helpers -----------------------------------------------------------

    def _node_world_path(self) -> Path:
        """A pristine copy of the node's world for read-only consultation.

        Created once per lease (the id is stable) and left for the run's
        lifetime — the lab may have diverged, consult(read=node) must see
        the purchased world as it was bought.
        """
        workspace_id = f"ro-node-{self.episode_id}"
        path = self.lab.provider.wt_root / workspace_id
        if not path.exists():
            self.lab.provider.create(WorkspaceSpec(
                workspace_id, self.node_sha))
        return path
