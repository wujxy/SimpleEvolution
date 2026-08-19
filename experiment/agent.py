"""Thin Claude CLI adapter over a prepared sandbox."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .contracts import ExecutionSandbox, ProcessRequest


class AgentError(RuntimeError):
    """Raised when the agent call fails or returns unparseable output."""

    def __init__(self, message: str, raw_output: str = "",
                 cause: str = ""):
        super().__init__(message)
        self.raw_output = raw_output
        self.cause = cause


@dataclass
class AgentResult:
    text: str
    usage: object = None


def _decode_stream(stdout: str) -> AgentResult:
    """Decode a Claude ``--output-format stream-json`` line stream.

    Each line is a JSON event.  The final ``result`` event carries the
    finished text and usage; tool-call events are present in the stream for
    full-fidelity L1 trace but do not affect the decoded text.
    """
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
    return AgentResult(text=text, usage=usage)


class Agent:
    def __init__(
        self,
        *,
        world: ExecutionSandbox,
        command: str = "claude",
        timeout_seconds: int = 1800,
        extra_args: list[str] | None = None,
        model: str | None = None,
        allowed_tools: str = "Read,Edit,Write,Bash",
        usage_observer: Callable[[object], None] | None = None,
        trace_store=None,
        invocation_id: str | None = None,
        role: str = "executor",
        identity: dict[str, str | None] | None = None,
    ):
        self.world = world
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.extra_args = list(extra_args or [])
        self.model = model
        self.allowed_tools = allowed_tools
        self.usage_observer = usage_observer
        self.trace_store = trace_store
        self.invocation_id = invocation_id
        self.role = role
        self.identity = identity or {}

    def _notify_usage(self, usage: object, label: str) -> None:
        if self.usage_observer is None:
            return
        try:
            self.usage_observer(usage)
        except Exception as exc:
            print(
                f"[telemetry] warning: {label} usage was not recorded: {exc}",
                flush=True,
            )

    def run_text(self, prompt: str, *, cwd: Path, label: str = "agent") -> str:
        """Run the agent, return its raw text."""
        return self._run(prompt, cwd=cwd, label=label).text

    def _start_trace(self) -> None:
        if self.trace_store is None or self.invocation_id is None:
            return
        try:
            self.trace_store.start_invocation(
                self.invocation_id,
                role=self.role,
                identity=self.identity,
            )
        except Exception as exc:
            print(f"[trace] start_invocation failed: {exc}", flush=True)

    def _append_trace_lines(self, stdout: str) -> None:
        if self.trace_store is None or self.invocation_id is None:
            return
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.trace_store.append_raw_line(self.invocation_id, line)
            except Exception as exc:
                print(f"[trace] append failed: {exc}", flush=True)

    def _run(self, prompt: str, *, cwd: Path, label: str) -> AgentResult:
        payload = [
            self.command, "-p",
            "--input-format", "text",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", self.allowed_tools,
        ]
        if self.model:
            payload += ["--model", self.model]
        payload += self.extra_args
        print(
            f"[{label}] claude call started "
            f"(timeout={self.timeout_seconds}s, world=/work)",
            flush=True,
        )
        self._start_trace()
        completed = self.world.run(ProcessRequest(
            tuple(payload), PurePosixPath("/work"), self.timeout_seconds,
            stdin=prompt, label=label,
        ))
        self._append_trace_lines(completed.stdout)
        result = _decode_stream(completed.stdout)
        self._notify_usage(result.usage, label)
        if completed.timed_out:
            raise AgentError(
                f"[{label}] timed out after {self.timeout_seconds}s\n"
                f"stderr: {completed.stderr.strip()[:2000]}",
                cause="timed_out",
            )
        if completed.exit_code != 0:
            raise AgentError(
                f"[{label}] claude exited {completed.exit_code}\n"
                f"stdout: {completed.stdout.strip()[:2000]}\n"
                f"stderr: {completed.stderr.strip()[:2000]}",
                cause="crashed",
            )
        print(
            f"[{label}] claude call finished "
            f"({completed.duration_seconds:.0f}s)", flush=True,
        )
        return result
