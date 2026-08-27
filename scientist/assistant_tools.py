"""One subprocess runtime for fresh research-team engagements.

The four durable roles share this runtime, but every engagement gets a fresh
Claude trajectory and an attributable id. Cognitive roles receive read-only
capabilities; Executor alone receives editing and shell capabilities. Raw
trajectories use the legacy ``.scientist/assistant`` archive path and never
enter the Scientist's active context.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .collaboration import ROLE_NAMES, build_collaboration_prompt

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_COGNITIVE_TOOLS = "Read,Grep,Glob,WebSearch,WebFetch,Task"
_EXECUTOR_TOOLS = "Read,Grep,Glob,Edit,Write,Bash,WebSearch,WebFetch"


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


# Hard ceiling for any single Executor engagement, in minutes — the default bounds
# only jobs that do not ask for more; a per-job timeout may run longer.
_WORK_TIMEOUT_MAX_MINUTES = 180


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
    goal: str = ""
    gate_block: str = ""

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
            goal=str(spec.get("goal") or ""),
            gate_block=str(spec.get("gate_block") or ""),
        )


@dataclass
class _Job:
    """One dispatched role engagement, until its report lands."""
    call_id: str
    role: str
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
    """Shared Claude subprocess runtime behind the four research roles."""

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
        print(f"[research-team] {label} {call_id} dispatched "
              f"(background)", flush=True)
        proc = subprocess.Popen(
            self._command_payload(allowed_tools),
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=handle,
            stderr=subprocess.STDOUT, env=env, text=True,
            # own process group: a time-box kill must take the whole
            # tree (claude's bash/bench children), not just the CLI —
            # orphaned grandchildren keep burning CPU and pollute the
            # measurements of everything still running
            start_new_session=True,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()
        return proc, raw_path, handle

    def engage(self, role: str, action: dict) -> dict:
        """Open one fresh, asynchronous role engagement."""
        if role not in ROLE_NAMES:
            return {"ok": False, "error": f"unknown collaborator role: {role}"}

        evidence_index = self.ledger.neutral_experiment_index()
        selected_experiments: list[dict] = []
        if not (role == "proposer" and action.get("scope") == "open"):
            for experiment_id in action.get("experiment_ids") or []:
                result = self.ledger.inspect_experiment(
                    {"experiment_id": experiment_id}
                )
                if result.get("ok"):
                    selected_experiments.append(result)
        try:
            prompt = build_collaboration_prompt(
                role,
                action,
                goal=self.config.goal or "(no goal stated)",
                gate_block=self.config.gate_block or "(no constraints stated)",
                current_judgment=self.ledger.current_judgment(),
                evidence_index=evidence_index,
                selected_experiments=selected_experiments,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        collaborator_id = self._next_call_id(role)
        work_dir = self.world.work
        side_dir: Path | None = None
        workspace = "read-only"
        allowed_tools = _COGNITIVE_TOOLS
        timeout = self.config.consult_timeout_seconds

        if role == "searcher":
            read = action.get("read", "none")
            if read not in {"none", "node", "lab"}:
                return {"ok": False, "error": "searcher.read must be none|node|lab"}
            if read == "none":
                work_dir = self.world.scratch
            elif read == "node":
                if self.config.node_world is None:
                    return {"ok": False, "error": "searcher read=node requires a node world"}
                work_dir = Path(self.config.node_world)
            workspace = str(read)
        elif role == "executor":
            workspace = str(action.get("workspace") or "current")
            if workspace not in {"current", "isolated"}:
                return {
                    "ok": False,
                    "error": "executor.workspace must be current|isolated",
                }
            allowed_tools = _EXECUTOR_TOOLS
            requested_minutes = action.get("timeout_minutes")
            try:
                minutes = int(requested_minutes) if requested_minutes else (
                    self.config.work_default_minutes
                )
            except (TypeError, ValueError):
                minutes = self.config.work_default_minutes
            timeout = max(1, min(minutes, _WORK_TIMEOUT_MAX_MINUTES)) * 60
            if workspace == "isolated":
                source = self.config.node_world or self.world.work
                side_dir = self.world.scratch / f"fresh-{collaborator_id}"
                # .scientist MUST be excluded: side_dir lives inside the
                # source tree, and copying the ledger/scratch into the
                # walk means copytree chases its own destination until
                # NAME_MAX (first live isolated dispatch died this way).
                # An isolated collaborator gets the world, not the PI's
                # records.
                shutil.copytree(
                    source, side_dir, dirs_exist_ok=False,
                    ignore=shutil.ignore_patterns(".scientist"),
                )
                work_dir = side_dir

        proc, raw_path, handle = self._spawn(
            collaborator_id,
            prompt=prompt,
            cwd=work_dir,
            allowed_tools=allowed_tools,
            label=role,
        )
        brief = str(action.get("brief") or "").strip()
        self._jobs.append(_Job(
            call_id=collaborator_id,
            role=role,
            instruction=brief,
            mode=workspace,
            fresh_note="",
            work_dir=work_dir,
            side_dir=side_dir,
            prompt=prompt,
            proc=proc,
            raw_path=raw_path,
            stdout_handle=handle,
            started=time.time(),
            timeout_seconds=timeout,
        ))
        self.ledger.note_assistant_call({
            "call_id": collaborator_id,
            "collaborator_id": collaborator_id,
            "episode_id": self.episode_id,
            "kind": role,
            "status": "dispatched",
            "question_digest": brief[:400],
        })
        return {
            "ok": True,
            "status": "running",
            "role": role,
            "collaborator_id": collaborator_id,
            "outstanding_jobs": [job.call_id for job in self._jobs],
        }

    def finished_pending(self) -> int:
        """How many outstanding jobs have their process exited, WITHOUT
        finalizing them. Observation only — finalization and report
        delivery stay with ``poll``, the conversation's single intake
        point. ``wait`` uses this to park the loop until mail arrives;
        it never delivers mail itself."""
        return sum(
            1 for job in self._jobs if job.proc.poll() is not None)

    @staticmethod
    def _kill_tree(job: "_Job") -> None:
        """Kill the engagement's whole process group and reap it."""
        try:
            os.killpg(job.proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                job.proc.kill()
            except OSError:
                pass
        try:
            job.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass

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
                self._kill_tree(job)
                reports.append(self._finalize(
                    job, error=f"{job.role} {job.call_id} exceeded its time box "
                    f"({job.timeout_seconds}s) and was stopped"))
            elif job.proc.returncode != 0:
                reports.append(self._finalize(
                    job, error=f"claude exited {job.proc.returncode}"))
            else:
                reports.append(self._finalize(job))
        self._jobs = still
        return reports

    def shutdown(self) -> int:
        """Abandon outstanding jobs (episode exit); returns how many.
        The transcript and any side-dir artifacts survive for recovery —
        a crash or cut_off must not destroy evidence that a resumed run
        or a successor could use."""
        abandoned = len(self._jobs)
        for job in self._jobs:
            self._kill_tree(job)
            try:
                job.stdout_handle.close()
            except OSError:
                pass
            self.ledger.note_assistant_call({
                "call_id": job.call_id,
                "episode_id": self.episode_id,
                "kind": job.role,
                "status": "abandoned",
                "question_digest": job.instruction[:400],
            })
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
        if error and job.side_dir is not None:
            # A failed engagement's artifacts are recoverable evidence —
            # keep the side dir and hand the PI pointers. Interpretation
            # stays with the Scientist (it may hand the pointers to a
            # Challenger or a fresh engagement); the harness never
            # summarizes what the trajectory "means".
            pass
        elif job.side_dir is not None:
            shutil.rmtree(job.side_dir, ignore_errors=True)
        if error:
            self._persist_raw(job.call_id, job.prompt, raw,
                              {"error": error})
            self._note(job.call_id, job.role,
                       f"{job.instruction} -> {error}", None)
            return {"ok": False, "call_id": job.call_id,
                    "collaborator_id": job.call_id, "role": job.role,
                    "status": "failed", "error": error,
                    "evidence": {
                        "transcript": (
                            f"/work/.scientist/assistant/{job.call_id}/"
                            "raw.txt"),
                        "prompt": (
                            f"/work/.scientist/assistant/{job.call_id}/"
                            "prompt.txt"),
                        "artifacts": (
                            "/work/.scientist/scratch/fresh-"
                            f"{job.call_id}" if job.side_dir is not None
                            else "/work"),
                        "ran_for_seconds": round(time.time() - job.started),
                    }}
        cap = self.config.distill_word_cap
        text, usage = _decode_stream(raw)
        tail = _parse_tail(text) or {}
        diff_summary, d_trunc = _cap_words(
            str(tail.get("diff_summary") or ""), cap)
        self_report, s_trunc = _cap_words(str(
            tail.get("report_digest")
            or tail.get("self_report_digest")
            or tail.get("answer_digest")
            or text
            or ""
        ), cap)
        metrics = tail.get("metrics") if isinstance(
            tail.get("metrics"), dict) else {}
        self._persist_raw(job.call_id, job.prompt, raw, {
            "mode": job.mode + job.fresh_note,
            "role": job.role,
            "collaborator_id": job.call_id,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics,
        })
        self._note(job.call_id, job.role,
                   f"{job.instruction} -> {self_report}", usage)
        return {
            "ok": True,
            "call_id": job.call_id,
            "collaborator_id": job.call_id,
            "role": job.role,
            "status": "done" if tail else "unparsed",
            "mode": job.mode + job.fresh_note,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "report_digest": self_report,
            "evidence": tail.get("evidence") or [],
            "artifacts": tail.get("artifacts") or [],
            "uncertainty": tail.get("uncertainty") or "",
            "recommended_follow_up": tail.get("recommended_follow_up") or "",
            "metrics": metrics,
            "truncated": bool(d_trunc or s_trunc),
            # The world itself is the truth channel; these numbers are the
            # assistant's own report.
            "channel": "belief",
        }
