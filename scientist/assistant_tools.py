"""The scientist's two hands on its claude assistant: consult and work.

One world, one filesystem: the assistant is an equal resident — it runs
the same ``claude`` CLI the image carries, with the same read/write
capabilities the scientist has. There is no sandbox, no mount choreography,
no per-call snapshot; the permission boundary of the consult channel is
expressed the one way claude itself offers — ``--allowedTools`` without
write tools. What the boundary means is unchanged: consult never writes
the world, work does.

The distillation contract is mechanical and carries over verbatim from
the host-side hands: the raw transcript lands under
``<world>/.scientist/assistant/<call_id>/`` and NEVER returns to the
researcher's context; the digest is hard-capped in words and ends with
one fenced JSON block. The ledger note (assistant_calls.jsonl) replaces
the DB rows; the world itself is the record of what work changed —
post-hoc readable, never audited in-flight.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# The consult channel carries no world-writing tools: the boundary is
# claude's own tool list, nothing else (there is no second layer).
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
Work INSIDE this world — the directory tree you start in is the world
itself (writable; build and self-measure in place; there is no version
control here, the files you leave ARE the record).
{measure_note}
Your scientist's instruction:
{instruction}

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

_BENCHMARK_NOTE = (
    "\nThis task has a benchmark: self-measure in the world (the "
    "eval scripts are there) and report the numbers you measured."
)


def _cap_words(text: str, cap: int) -> tuple[str, bool]:
    words = text.split()
    if len(words) <= cap:
        return text, False
    return " ".join(words[:cap]) + " …[truncated at cap]", True


def _parse_tail(text: str) -> dict | None:
    for raw in reversed(_JSON_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _decode_stream(stdout: str) -> tuple[str, object]:
    """Decode a claude ``--output-format stream-json`` line stream.

    The final ``result`` event carries the finished text and usage."""
    text = stdout
    usage: object = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event.get("result")
            if isinstance(result, str):
                text = result
            usage = event.get("usage")
    return text, usage


@dataclass
class AssistantConfig:
    command: str = "claude"
    model: str | None = None
    effort: str | None = None
    node_world: Path | None = None      # pristine copy (harness-provided)
    env: dict | None = None             # merged over os.environ
    distill_word_cap: int = 300
    consult_timeout_seconds: int = 900
    work_default_minutes: int = 30

    @classmethod
    def from_spec(cls, spec: dict) -> "AssistantConfig":
        assistant = dict(spec.get("assistant") or {})
        node_world = assistant.get("node_world")
        return cls(
            command=str(assistant.get("command") or "claude"),
            model=assistant.get("model") or None,
            effort=assistant.get("effort") or None,
            node_world=Path(node_world) if node_world else None,
            env=assistant.get("env") or None,
            distill_word_cap=int(
                (spec.get("budget") or {}).get(
                    "distill_word_cap", 300)),
            consult_timeout_seconds=int(
                (spec.get("budget") or {}).get(
                    "consult_timeout_seconds", 900)),
            work_default_minutes=int(
                (spec.get("budget") or {}).get(
                    "work_default_minutes", 30)),
        )


@dataclass
class _Job:
    """One dispatched background work call, until its report lands."""
    call_id: str
    instruction: str
    mode: str
    fresh_note: str
    work_dir: Path
    side_dir: Path | None
    prompt: str
    proc: subprocess.Popen
    raw_path: Path
    stdout_handle: object
    started: float
    timeout_seconds: int


class InWorldAssistant:
    """consult / work as claude subprocesses inside the lived-in world.

    consult is synchronous (a question blocks the decision that asked
    it); work is dispatched to the background — the strongest executor
    there is should never make you wait — and its report is pumped back
    into the conversation by ``poll`` between model calls."""

    def __init__(
        self,
        *,
        world,
        config: AssistantConfig,
        ledger,
        episode_id: str = "ep",
        has_benchmark: bool = True,
    ):
        self.world = world
        self.config = config
        self.ledger = ledger
        self.episode_id = episode_id
        self.has_benchmark = has_benchmark
        self._counter = 0
        self._jobs: list[_Job] = []

    # -- plumbing ----------------------------------------------------------

    def _next_call_id(self, kind: str) -> str:
        self._counter += 1
        return f"{kind}-{self.episode_id}-{self._counter:03d}"

    def _raw_dir(self, call_id: str) -> Path:
        d = self.world.work / ".scientist" / "assistant" / call_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_claude(
        self, call_id: str, *, prompt: str, cwd: Path,
        allowed_tools: str, timeout: int, label: str,
    ) -> tuple[str, object, str]:
        """Run one claude call; returns (text, usage, error). ``error`` is
        empty on success — failures return as observations, not crashes:
        the seat sees the failure and decides what it means."""
        payload = [
            self.config.command, "-p",
            "--input-format", "text",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", allowed_tools,
        ]
        if self.config.model:
            payload += ["--model", self.config.model]
        if self.config.effort:
            payload += ["--effort", self.config.effort]

        env = dict(os.environ)
        if self.config.env:
            env.update(
                {str(k): str(v) for k, v in self.config.env.items()})
        print(f"[assistant] {label} {call_id} started "
              f"(timeout={timeout}s)", flush=True)
        try:
            completed = subprocess.run(
                payload, cwd=str(cwd), input=prompt, text=True,
                capture_output=True, timeout=timeout, env=env,
            )
        except subprocess.TimeoutExpired:
            return "", None, f"{label} timed out after {timeout}s"
        except OSError as exc:
            return "", None, f"{label} failed to start: {exc}"
        stdout = completed.stdout or ""
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()[:2000]
            return stdout, None, (
                f"{label}: claude exited {completed.returncode}: {detail}"
            )
        text, usage = _decode_stream(stdout)
        return text, usage, ""

    def _persist_raw(self, call_id: str, prompt: str, raw: str,
                     digest: dict) -> None:
        d = self._raw_dir(call_id)
        (d / "prompt.txt").write_text(prompt, encoding="utf-8")
        (d / "raw.txt").write_text(raw or "", encoding="utf-8")
        (d / "digest.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _note(self, call_id: str, kind: str, digest: str,
              usage: object) -> None:
        self.ledger.note_assistant_call({
            "call_id": call_id,
            "episode_id": self.episode_id,
            "kind": kind,
            "question_digest": digest[:400],
            "usage": usage if isinstance(usage, dict) else None,
        })

    # -- consult (belief channel: never writes the world) ------------------

    def consult(self, action: dict) -> dict:
        question = action.get("question")
        if not isinstance(question, str) or not question.strip():
            return {"ok": False,
                    "error": "consult.question must be non-empty"}
        read = action.get("read", "none")
        if read not in {"none", "node", "lab"}:
            return {
                "ok": False,
                "error": f"consult.read must be none|node|lab, got {read!r}",
            }
        context = action.get("context") or ""
        if not isinstance(context, str):
            return {"ok": False, "error": "consult.context must be a string"}
        call_id = self._next_call_id("consult")

        cwd = self.world.scratch
        world_note = ""
        if read == "node":
            if self.config.node_world is None:
                return {
                    "ok": False,
                    "error": "no pristine node world is available in "
                             "this run (read=node needs one)",
                }
            cwd = Path(self.config.node_world)
            world_note = (
                "\nThe pristine copy of the world under study is the "
                "tree you start in."
            )
        elif read == "lab":
            cwd = self.world.work
            world_note = (
                "\nThe scientist's current laboratory (work in progress) "
                "is the tree you start in."
            )

        cap = self.config.distill_word_cap
        prompt = _CONSULT_PROMPT.format(
            question=question.strip(), context=context or "(none)",
            world_note=world_note, cap=cap,
        )
        raw, usage, error = self._run_claude(
            call_id, prompt=prompt, cwd=cwd,
            allowed_tools=_CONSULT_TOOLS,
            timeout=self.config.consult_timeout_seconds, label="consult",
        )
        if error:
            self._persist_raw(call_id, prompt, raw, {"error": error})
            self._note(call_id, "consult", question, usage)
            return {"ok": False, "status": "failed", "error": error,
                    "call_id": call_id}
        tail = _parse_tail(raw) or {}
        digest = str(tail.get("answer_digest") or raw or "").strip()
        digest, truncated = _cap_words(digest, cap)
        sources = tail.get("sources") or []
        self._persist_raw(call_id, prompt, raw, {
            "answer_digest": digest, "sources": sources,
            "truncated": truncated,
        })
        self._note(call_id, "consult", f"{question} -> {digest}", usage)
        return {
            "ok": True,
            "call_id": call_id,
            # The belief-channel stamp: this is the assistant's opinion,
            # not verified fact — adopting it is the seat's judgment.
            "channel": "belief",
            "answer_digest": digest,
            "sources": sources,
            "truncated": truncated,
        }

    # -- work (the lab channel, async: dispatch, keep working, report
    #    arrives later as its own message) -----------------------------------

    def _command_payload(self, allowed_tools: str) -> list[str]:
        payload = [
            self.config.command, "-p",
            "--input-format", "text",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", allowed_tools,
        ]
        if self.config.model:
            payload += ["--model", self.config.model]
        if self.config.effort:
            payload += ["--effort", self.config.effort]
        return payload

    def _spawn(
        self, call_id: str, *, prompt: str, cwd: Path,
        allowed_tools: str, label: str,
    ):
        """Start one claude call in the background. stdout (the
        stream-json transcript) grows into the raw file; the job is
        finalized later by ``poll``."""
        env = dict(os.environ)
        if self.config.env:
            env.update(
                {str(k): str(v) for k, v in self.config.env.items()})
        raw_path = self._raw_dir(call_id) / "raw.txt"
        handle = raw_path.open("wb")
        print(f"[assistant] {label} {call_id} dispatched "
              f"(background)", flush=True)
        proc = subprocess.Popen(
            self._command_payload(allowed_tools),
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=handle,
            stderr=subprocess.STDOUT, env=env, text=True,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        return proc, raw_path, handle

    def work(self, action: dict) -> dict:
        """Dispatch a job to the assistant; returns a receipt at once.

        The report arrives later, as its own message, when ``poll``
        finalizes the job. The time box is infrastructure (config), not
        something the seat sees or sizes."""
        instruction = action.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            return {"ok": False,
                    "error": "work.instruction must be non-empty"}
        mode = action.get("mode", "continue")
        if mode not in {"continue", "fresh"}:
            return {
                "ok": False,
                "error": f"work.mode must be continue|fresh, got {mode!r}",
            }
        call_id = self._next_call_id("work")
        timeout = max(60, int(self.config.work_default_minutes * 60))

        work_dir = self.world.work
        fresh_note = ""
        side_dir: Path | None = None
        if mode == "fresh":
            source = (
                self.config.node_world
                if self.config.node_world is not None else self.world.work
            )
            if self.config.node_world is None:
                fresh_note = (
                    " (copied from the CURRENT world — no pristine copy is "
                    "available in this run)"
                )
            side_dir = self.world.scratch / f"fresh-{call_id}"
            shutil.copytree(source, side_dir, dirs_exist_ok=False)
            work_dir = side_dir

        cap = self.config.distill_word_cap
        measure_note = _BENCHMARK_NOTE if self.has_benchmark else ""
        prompt = _WORK_PROMPT.format(
            measure_note=measure_note, instruction=instruction.strip(),
            cap=cap,
        )
        proc, raw_path, handle = self._spawn(
            call_id, prompt=prompt, cwd=work_dir,
            allowed_tools=_WORK_TOOLS, label="work",
        )
        self._jobs.append(_Job(
            call_id=call_id, instruction=instruction.strip(), mode=mode,
            fresh_note=fresh_note, work_dir=work_dir, side_dir=side_dir,
            prompt=prompt, proc=proc, raw_path=raw_path,
            stdout_handle=handle, started=time.time(),
            timeout_seconds=timeout,
        ))
        self.ledger.note_assistant_call({
            "call_id": call_id,
            "episode_id": self.episode_id,
            "kind": "work",
            "status": "dispatched",
            "question_digest": instruction.strip()[:400],
        })
        return {
            "ok": True,
            "call_id": call_id,
            "status": "running",
            "mode": mode + fresh_note,
            "outstanding_jobs": [job.call_id for job in self._jobs],
            "note": "your assistant took the brief and works on its own; "
                    "keep reading and thinking — its report arrives as its "
                    "own message when the job is done (jobs not yet "
                    "reported are still running; wait for one with wait)",
        }

    def finished_pending(self) -> int:
        """How many outstanding jobs have their process exited, WITHOUT
        finalizing them. Observation only — finalization and report
        delivery stay with ``poll``, the conversation's single intake
        point. ``wait`` uses this to park the loop until mail arrives;
        it never delivers mail itself."""
        return sum(
            1 for job in self._jobs if job.proc.poll() is not None)

    def poll(self) -> list[dict]:
        """Finalize finished/timed-out jobs; return their reports.

        Called by the loop between model calls — completed reports become
        user messages in the conversation (the mail arriving)."""
        reports: list[dict] = []
        still: list[_Job] = []
        for job in self._jobs:
            if job.proc.poll() is None:
                if time.time() - job.started <= job.timeout_seconds:
                    still.append(job)
                    continue
                job.proc.kill()
                job.proc.wait()
                reports.append(self._finalize(
                    job, error=f"work {job.call_id} exceeded its time box "
                    f"({job.timeout_seconds}s) and was stopped"))
            elif job.proc.returncode != 0:
                reports.append(self._finalize(
                    job, error=f"claude exited {job.proc.returncode}"))
            else:
                reports.append(self._finalize(job))
        self._jobs = still
        return reports

    def shutdown(self) -> int:
        """Abandon outstanding jobs (episode exit); returns how many."""
        abandoned = len(self._jobs)
        for job in self._jobs:
            try:
                job.proc.kill()
            except OSError:
                pass
            try:
                job.stdout_handle.close()
            except OSError:
                pass
            self.ledger.note_assistant_call({
                "call_id": job.call_id,
                "episode_id": self.episode_id,
                "kind": "work",
                "status": "abandoned",
                "question_digest": job.instruction[:400],
            })
            if job.side_dir is not None:
                shutil.rmtree(job.side_dir, ignore_errors=True)
        self._jobs = []
        return abandoned

    def _finalize(self, job: "_Job", error: str = "") -> dict:
        try:
            job.stdout_handle.close()
        except OSError:
            pass
        try:
            raw = job.raw_path.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        if job.side_dir is not None:
            shutil.rmtree(job.side_dir, ignore_errors=True)
        if error:
            self._persist_raw(job.call_id, job.prompt, raw,
                              {"error": error})
            self._note(job.call_id, "work",
                       f"{job.instruction} -> {error}", None)
            return {"ok": False, "call_id": job.call_id,
                    "status": "failed", "error": error}
        cap = self.config.distill_word_cap
        text, usage = _decode_stream(raw)
        tail = _parse_tail(text) or {}
        diff_summary, d_trunc = _cap_words(
            str(tail.get("diff_summary") or ""), cap)
        self_report, s_trunc = _cap_words(
            str(tail.get("self_report_digest") or text or ""), cap)
        metrics = tail.get("metrics") if isinstance(
            tail.get("metrics"), dict) else {}
        self._persist_raw(job.call_id, job.prompt, raw, {
            "mode": job.mode + job.fresh_note,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics,
        })
        self._note(job.call_id, "work",
                   f"{job.instruction} -> {self_report}", usage)
        return {
            "ok": True,
            "call_id": job.call_id,
            "status": "done" if tail else "unparsed",
            "mode": job.mode + job.fresh_note,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics,
            "truncated": bool(d_trunc or s_trunc),
            # The world itself is the truth channel; these numbers are the
            # assistant's own report.
            "channel": "belief",
        }
