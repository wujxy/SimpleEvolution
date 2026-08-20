"""Local subprocess scheduler adapter for SimpleEvolution.

This is the default job backend for smoke tests and local development.  It
writes a ``WorkerRequest`` manifest for each job and spawns the appropriate
worker module as a subprocess.  HTCondor deployments can replace this with
a thin adapter that stages the same manifest/result files and submits via
condor_submit.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import EvolutionConfig
from .envelope import WorkerRequest, WorkerResult, read_result, write_request


@dataclass(frozen=True)
class LocalJobSpec:
    request: WorkerRequest
    manifest_path: Path
    result_path: Path
    argv: list[str]


class LocalSubmitter:
    """Launch proposer/experiment workers as local subprocesses.

    Implements the callable interface the Scheduler injects for
    ``submit_proposer`` and ``submit_experiment``.
    """

    def __init__(
        self,
        run_dir: Path,
        config: EvolutionConfig,
        *,
        python: str = "python",
    ):
        self.run_dir = Path(run_dir)
        self.config = config
        self.python = python

    def submit_proposer(self, allocation_id: str, payload: Mapping[str, Any]) -> str:
        """Write a proposer manifest and launch ``python -m proposer.cli``."""
        manifest_dir = self.run_dir / "proposer_allocations" / allocation_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "manifest.json"
        result_path = manifest_dir / "result.json"

        full_payload = {**self._common_payload(), **dict(payload)}
        full_payload.setdefault("run_dir", str(self.run_dir))
        full_payload.setdefault("repo_path", str(self.config.repo_path))
        full_payload.setdefault("runtime_image", str(self.config.runtime_image))
        full_payload.setdefault("runtime_binds", list(self.config.runtime_binds))
        full_payload.setdefault("read_only_binds", list(self.config.read_only_binds))
        full_payload.setdefault("goal", self.config.goal)
        full_payload.setdefault("editable_paths", list(self.config.editable_paths))
        full_payload.setdefault("frozen_paths", list(self.config.frozen_paths))
        full_payload.setdefault("gate_block", self.config.gate_block)
        full_payload.setdefault("proposal_slots", self.config.proposal_slots)
        full_payload.setdefault("scientist_steps", self.config.scientist_steps)
        full_payload.setdefault("agent_timeout_seconds", self.config.agent_timeout_seconds)
        full_payload.setdefault("command_timeout_seconds", self.config.command_timeout_seconds)
        full_payload.setdefault("command_output_cap_chars", self.config.command_output_cap_chars)
        full_payload.setdefault("eval_timeout_seconds", self.config.eval_timeout_seconds)
        full_payload.setdefault("researcher", dict(self.config.researcher))
        full_payload.setdefault("context", dict(self.config.context))
        full_payload.setdefault("prompt_dir", str(self.config.prompt_dir) if self.config.prompt_dir else "")

        request = WorkerRequest(
            kind="proposer",
            request_id=allocation_id,
            payload=full_payload,
            result_path=result_path,
        )
        return self._launch(request, manifest_path, result_path, ["-m", "proposer.cli"])

    def submit_experiment(self, experiment_id: str, payload: Mapping[str, Any]) -> str:
        """Write an experiment manifest and launch ``python -m experiment.cli``."""
        manifest_dir = self.run_dir / "experiments" / experiment_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "manifest.json"
        result_path = manifest_dir / "result.json"

        full_payload = {**self._common_payload(), **dict(payload)}
        full_payload.setdefault("run_dir", str(self.run_dir))
        full_payload.setdefault("repo_path", str(self.config.repo_path))
        full_payload.setdefault("runtime_image", str(self.config.runtime_image))
        full_payload.setdefault("editable_paths", list(self.config.editable_paths))
        full_payload.setdefault("frozen_paths", list(self.config.frozen_paths))
        full_payload.setdefault("eval_commands", list(self.config.eval_commands))
        full_payload.setdefault("metrics_schema", dict(self.config.metrics_schema))
        full_payload.setdefault("read_only_binds", list(self.config.read_only_binds))
        full_payload.setdefault("agent_timeout_seconds", self.config.agent_timeout_seconds)
        full_payload.setdefault("eval_timeout_seconds", self.config.eval_timeout_seconds)
        full_payload.setdefault("executor", dict(self.config.executor))

        request = WorkerRequest(
            kind="experiment",
            request_id=experiment_id,
            payload=full_payload,
            result_path=result_path,
        )
        return self._launch(request, manifest_path, result_path, ["-m", "experiment.cli"])

    def poll(self, spec: LocalJobSpec) -> WorkerResult | None:
        """Return the parsed result if the worker has finished."""
        if not spec.result_path.exists():
            return None
        return read_result(spec.result_path, expected=spec.request)

    def _common_payload(self) -> dict[str, Any]:
        """Fields that are shared by both worker kinds."""
        return {}

    def _launch(
        self,
        request: WorkerRequest,
        manifest_path: Path,
        result_path: Path,
        argv: list[str],
    ) -> str:
        write_request(manifest_path, request)
        # Capture worker stdout/stderr to a log next to its manifest so a
        # crash is diagnosable.  (The scheduler only polls ``result.json`` and
        # never inspects process state, so a silent crash otherwise leaves the
        # run hung forever.)
        log_file = open(manifest_path.parent / "worker.log", "ab")
        try:
            subprocess.Popen(
                [self.python, *argv, "--manifest", str(manifest_path)],
                stdout=log_file,
                stderr=log_file,
            )
        finally:
            log_file.close()
        return str(result_path)
