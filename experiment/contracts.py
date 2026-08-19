"""Neutral contracts for the experiment execution boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol


class WorkspaceError(RuntimeError):
    pass


class SandboxError(RuntimeError):
    pass


class SandboxLaunchError(SandboxError):
    pass


@dataclass(frozen=True)
class WorkspaceSpec:
    workspace_id: str
    revision: str


@dataclass(frozen=True)
class SourceWorkspace:
    workspace_id: str
    path: Path
    base_sha: str


@dataclass(frozen=True)
class ChangeSet:
    paths: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class CommitRequest:
    experiment_id: str
    proposal_id: str
    parent_sha: str
    changed_paths: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str | None = None
    output: str = ""
    self_report: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class EvaluationResult:
    text: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    returncodes: tuple[int, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class GateResult:
    passed: bool | None
    detail: str = ""


@dataclass(frozen=True)
class GateDecision:
    results: Mapping[str, GateResult]
    passed: bool


@dataclass(frozen=True)
class ExperimentRequest:
    experiment_id: str
    proposal_id: str
    parent_node_id: str
    parent_sha: str
    proposal: str
    repo_path: Path
    run_dir: Path
    editable_paths: tuple[str, ...]
    frozen_paths: tuple[str, ...]
    eval_commands: tuple[str, ...]
    metrics_schema: Mapping[str, Any]
    runtime_image: Path
    agent_timeout_seconds: int = 3600
    eval_timeout_seconds: int = 600
    attempt: int = 1
    attempt_id: str = ""
    executor: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    proposal_id: str
    parent_node_id: str
    parent_sha: str
    status: str
    sha: str | None
    metrics: Mapping[str, Any]
    gate: GateDecision
    eval_block: str
    changed_paths: tuple[PurePosixPath, ...]
    execution: ExecutionResult


class MountMode(str, Enum):
    READ_ONLY = "ro"
    READ_WRITE = "rw"


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: PurePosixPath
    mode: MountMode = MountMode.READ_ONLY


@dataclass(frozen=True)
class SandboxSpec:
    image: Path
    environment: Mapping[str, str] = field(default_factory=dict)
    network: bool = True


@dataclass(frozen=True)
class ProcessRequest:
    argv: tuple[str, ...]
    cwd: PurePosixPath
    timeout_seconds: int
    stdin: str | None = None
    label: str = ""


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


class ExecutionSandbox(Protocol):
    def run(self, request: ProcessRequest) -> ProcessResult:
        ...
