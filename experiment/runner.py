"""Experiment runner: worktree → executor → commit → eval → gate."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .agent import Agent
from .apptainer import (
    ApptainerSandbox,
    SandboxSpec,
    executor_environment,
    forwarded_payload_env,
)
from .contracts import (
    CommitRequest,
    EvaluationResult,
    ExecutionResult,
    ExperimentRequest,
    ExperimentResult,
    GateDecision,
    GateResult,
    MountMode,
    MountSpec,
    SourceWorkspace,
    WorkspaceSpec,
)
from .evaluator import run_eval
from .executor import AgentExecutor, ExecutorConfig
from .gate import GateSpec, apply_gates, paths_allowed
from .git_worktree import GitWorkspaceProvider


class InfraFailure(RuntimeError):
    """An infrastructure failure (executor crash / timeout / API failure).

    Distinct from a scientific result: it must land on the Attempt table and
    leave the Experiment open for retry (§16/§17), never on the Experiment's
    scientific status.
    """


class ExperimentRunner:
    """Run one experiment end-to-end and return an ExperimentResult."""

    def __init__(self, request: ExperimentRequest):
        self.request = request

    def run(self) -> ExperimentResult:
        workspace: SourceWorkspace | None = None
        provider = GitWorkspaceProvider(
            self.request.run_dir,
            self.request.repo_path,
        )
        try:
            workspace = provider.create(WorkspaceSpec(
                self.request.experiment_id,
                self.request.parent_sha,
            ))

            execution = self._run_executor(workspace)
            if execution.status != "EXECUTED":
                raise InfraFailure(
                    execution.reason or "executor did not produce a valid result"
                )

            changed_paths = provider.inspect(workspace).paths
            changed_strs = {p.as_posix() for p in changed_paths}
            if not changed_strs:
                return self._result(
                    status="NO_CHANGE",
                    execution=execution,
                    evaluation=None,
                    gate=None,
                    sha=None,
                    changed_paths=(),
                )

            if not paths_allowed(changed_strs, list(self.request.editable_paths)):
                return self._result(
                    status="GATE_REJECTED",
                    execution=execution,
                    evaluation=None,
                    gate=GateDecision(
                        results={"PATHS": GateResult(False, "changed non-editable files")},
                        passed=False,
                    ),
                    sha=None,
                    changed_paths=(),
                )

            sha = provider.commit(workspace, CommitRequest(
                experiment_id=self.request.experiment_id,
                proposal_id=self.request.proposal_id,
                parent_sha=self.request.parent_sha,
                changed_paths=changed_paths,
            ))

            evaluation = self._run_evaluator(workspace)
            gate_spec = GateSpec(
                objective_key=(
                    self.request.metrics_schema.get("objective") or {}
                ).get("key", "OBJECTIVE"),
                gate_keys=tuple(
                    g["key"] for g in (self.request.metrics_schema.get("gates") or [])
                    if g.get("key")
                ),
            )
            gate = apply_gates(evaluation, gate_spec)

            status = "COMPLETED" if gate.passed else "GATE_REJECTED"
            return self._result(
                status=status,
                execution=execution,
                evaluation=evaluation,
                gate=gate,
                sha=sha,
                changed_paths=changed_paths,
            )
        finally:
            if workspace is not None:
                provider.remove(workspace)

    def _trace_store(self):
        """Build a bound L1 TraceStore (import cycle safe)."""
        from simpleevo.trace.store import TraceStore

        return TraceStore(self.request.run_dir)

    def _usage_observer(self):
        """Bound executor token-usage recorder (import cycle safe)."""
        from simpleevo.trace.usage import UsageRecorder

        recorder = UsageRecorder(self.request.run_dir)
        return lambda usage: recorder.record("executor", usage)

    def _run_executor(self, workspace: SourceWorkspace) -> ExecutionResult:
        sandbox = ApptainerSandbox(userns=True)
        executor_cfg = dict(self.request.executor)
        builder = sandbox.bind(
            SandboxSpec(
                image=self.request.runtime_image,
                environment=executor_environment(
                    base_url=executor_cfg.get("base_url"),
                    max_output_tokens=64000,
                ),
                network=True,
            ),
            mounts=self._mounts(workspace, writable=False),
        )
        agent = Agent(
            world=builder,
            command="claude",
            timeout_seconds=self.request.agent_timeout_seconds,
            allowed_tools="Read,Edit,Write,Bash",
            model=executor_cfg.get("model") or None,
            trace_store=self._trace_store(),
            usage_observer=self._usage_observer(),
            invocation_id=(
                f"experiment-{self.request.attempt_id}"
                if self.request.attempt_id
                else f"experiment-{self.request.experiment_id}"
            ),
            role="executor",
            identity={
                "experiment_id": self.request.experiment_id,
                "proposal_id": self.request.proposal_id,
                "parent_node_id": self.request.parent_node_id,
                "attempt_id": self.request.attempt_id,
                "attempt": str(self.request.attempt),
            },
        )
        executor = AgentExecutor(agent, ExecutorConfig(
            goal="",  # filled below from request if available
            gate_block="",
        ))
        return executor.execute(
            type("R", (), {
                "experiment_id": self.request.experiment_id,
                "proposal_id": self.request.proposal_id,
                "proposal": self.request.proposal,
                "workspace": workspace,
            })()
        )

    def _run_evaluator(self, workspace: SourceWorkspace) -> EvaluationResult:
        sandbox = ApptainerSandbox(userns=True)
        builder = sandbox.bind(
            SandboxSpec(
                image=self.request.runtime_image,
                environment={
                    k: v for k, v in forwarded_payload_env().items()
                    if not k.startswith("ANTHROPIC_")
                },
                network=True,
            ),
            mounts=self._mounts(workspace, writable=True),
        )
        result = run_eval(
            list(self.request.eval_commands),
            world=builder,
            metrics_schema=self.request.metrics_schema,
            timeout_seconds=self.request.eval_timeout_seconds,
        )
        return EvaluationResult(
            result.text,
            result.metrics,
            result.returncodes,
        )

    def _mounts(self, workspace: SourceWorkspace, *, writable: bool) -> tuple[MountSpec, ...]:
        mounts = [
            MountSpec(
                source=workspace.path,
                target=PurePosixPath("/work"),
                mode=MountMode.READ_WRITE if writable else MountMode.READ_ONLY,
            ),
        ]
        repo = GitWorkspaceProvider(self.request.run_dir, self.request.repo_path).repo
        mounts.append(MountSpec(
            source=repo,
            target=PurePosixPath("/repo"),
            mode=MountMode.READ_ONLY,
        ))
        for rel in self.request.editable_paths:
            src = workspace.path / rel
            if src.exists():
                mounts.append(MountSpec(
                    source=src,
                    target=PurePosixPath("/work") / rel,
                    mode=MountMode.READ_WRITE,
                ))
        return tuple(mounts)

    def _result(
        self,
        *,
        status: str,
        execution: ExecutionResult,
        evaluation: EvaluationResult | None,
        gate: GateDecision | None,
        sha: str | None,
        changed_paths: tuple[PurePosixPath, ...],
    ) -> ExperimentResult:
        return ExperimentResult(
            experiment_id=self.request.experiment_id,
            proposal_id=self.request.proposal_id,
            parent_node_id=self.request.parent_node_id,
            parent_sha=self.request.parent_sha,
            status=status,
            sha=sha,
            metrics=evaluation.metrics if evaluation else {},
            gate=gate or GateDecision({}, False),
            eval_block=evaluation.text if evaluation else execution.output,
            changed_paths=changed_paths,
            execution=execution,
        )
