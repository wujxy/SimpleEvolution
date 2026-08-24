"""Shared job-submission contract for every SimpleEvolution launch backend.

The Scheduler talks to one interface for proposer, experiment, Supervisor, and
Integrator workers, each returning the result path it will later poll —
regardless of whether the worker runs as a local subprocess or a condor job.
This module holds everything the two backends share so the only thing a
backend implements is the single seam ``_launch`` (how a staged manifest
becomes a running worker).

Interface parity is the point: ``LocalSubmitter`` and ``HTCondorSubmitter``
are both thin subclasses of ``BaseSubmitter`` and both expose the same
methods (the four submit methods / poll / probe_job /
remove_job).  Backend-specific concerns stay inside the subclass.
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..config import EvolutionConfig
from .envelope import WorkerRequest, WorkerResult, read_result, write_request


@dataclass(frozen=True)
class JobSpec:
    """One submitted job's scheduler-side identity (transport-neutral).

    ``result_path`` is the durable contract: the worker writes its envelope
    there; the Scheduler polls that file.  ``job_id`` is backend-specific —
    None for local subprocesses, ``<cluster>.<proc>`` for condor.
    """

    request: WorkerRequest
    manifest_path: Path
    result_path: Path
    argv: list[str]
    kind: str = ""                    # "proposer" | "experiment"
    work_id: str = ""
    job_id: str | None = None


class BaseSubmitter(ABC):
    """Common manifest staging, payload assembly and result layout.

    Subclasses implement ``_launch`` and pick backend-specific knobs
    (``backend`` name, ``presumes_dead_on_startup``).
    """

    #: Reported by workers into ``execution.scheduler`` (--backend arg).
    backend = "local"
    #: Does an in-flight worker survive this scheduler process?  Local
    #: subprocesses die with their parent, so on startup every ``running``
    #: attempt is presumed dead.  Condor jobs outlive the scheduler, so startup
    #: keeps ``running`` attempts and reconciles each against the real queue.
    presumes_dead_on_startup = True

    def __init__(
        self,
        run_dir: str | Path,
        config: EvolutionConfig,
        *,
        python: str | None = None,
    ):
        self.run_dir = Path(run_dir)
        self.config = config
        self.python = python or sys.executable

    # ------------------------------------------------------------------
    # Public interface (the Scheduler's only view of a backend)
    # ------------------------------------------------------------------

    def submit_proposer(self, allocation_id: str, payload: Mapping[str, Any]) -> str:
        """Stage a proposer manifest and launch the proposer worker.

        Returns the result path the Scheduler will poll.
        """
        return self._submit("proposer", allocation_id, payload, self._proposer_defaults())

    def submit_experiment(self, experiment_id: str, payload: Mapping[str, Any]) -> str:
        """Stage an experiment manifest and launch the experiment worker.

        Returns the result path the Scheduler will poll.
        """
        return self._submit("experiment", experiment_id, payload, self._experiment_defaults())

    def submit_supervisor(self, decision_id: str, payload: Mapping[str, Any]) -> str:
        """Stage one persistent Supervisor wake-up turn (growth gate)."""
        return self._submit("supervisor", decision_id, payload, self._proposer_defaults())

    def submit_integrator(self, request_id: str, payload: Mapping[str, Any]) -> str:
        """Stage one request-scoped temporary Integrator worker."""
        return self._submit("integrator", request_id, payload, self._proposer_defaults())

    def submit_baseline(self, run_id: str, payload: Mapping[str, Any]) -> str:
        """Submit the run-start baseline as an eval-only experiment job.

        Same manifest/result layout and the same launch seam as every other
        worker, so under condor the baseline lands on the same machine class
        (requirements, memory, schedd) as every candidate eval — the anchor
        SPEED_MS and the candidates' SPEED_MS are measured by the same
        population.  The payload carries ``eval_only``; the experiment worker
        skips its executor and runs only the eval commands.
        """
        return self._submit("experiment", run_id, payload, self._experiment_defaults())

    def poll(self, spec: JobSpec) -> WorkerResult | None:
        """Return the parsed worker result once ``result.json`` exists."""
        if not spec.result_path.exists():
            return None
        return read_result(spec.result_path, expected=spec.request)

    def probe_job(self, logical_work_id: str, work_kind: str) -> str:
        """Live state of an in-flight job: ``running | held | gone | unknown``.

        The Reconciler uses this to decide whether missing-result work is still
        alive (wait) or died on the cluster (mark infra-failed -> retry).
        Local cannot observe its subprocesses, so it always reports ``unknown``
        (result-file polling plus mark_running_attempts_lost on restart are the
        local fallbacks).  Condor overrides this with condor_q.
        """
        return "unknown"

    def remove_job(self, logical_work_id: str, work_kind: str) -> None:
        """Best-effort cleanup of a dead job (no-op for local subprocesses)."""

    # ------------------------------------------------------------------
    # Shared machinery
    # ------------------------------------------------------------------

    def _submit(
        self,
        kind: str,
        work_id: str,
        payload: Mapping[str, Any],
        defaults: Mapping[str, Any],
    ) -> str:
        manifest_dir, manifest_path, result_path = self._paths(kind, work_id)
        full = dict(payload)
        for key, value in defaults.items():
            full.setdefault(key, value)
        request = WorkerRequest(
            kind=kind,
            request_id=work_id,
            payload=full,
            result_path=result_path,
        )
        argv = self._worker_argv(kind, manifest_path)
        write_request(manifest_path, request)
        self._launch(kind, work_id, request, manifest_path, result_path, argv)
        return str(result_path)

    def _paths(self, kind: str, work_id: str) -> tuple[Path, Path, Path]:
        """(manifest_dir, manifest.json, result.json) for a logical work id."""
        if kind == "proposer":
            directory = self.run_dir / "proposer_allocations" / work_id
        elif kind == "supervisor":
            directory = self.run_dir / "supervisor_decisions" / work_id
        elif kind == "integrator":
            directory = self.run_dir / "integration_requests" / work_id
        else:
            directory = self.run_dir / "experiments" / work_id
        return directory, directory / "manifest.json", directory / "result.json"

    def _worker_argv(self, kind: str, manifest_path: Path) -> list[str]:
        """Worker argv (before python / job.sh).  --backend is transport
        metadata the worker reports into ``execution.scheduler``."""
        modules = {
            "proposer": "proposer.cli",
            "supervisor": "proposer.supervisor_cli",
            "integrator": "proposer.integrator_cli",
            "experiment": "experiment.cli",
        }
        module = modules[kind]
        return [
            "-m", module,
            "--manifest", str(manifest_path),
            "--backend", self.backend,
        ]

    def _proposer_defaults(self) -> dict[str, Any]:
        """Config-derived payload fields the proposer worker needs."""
        cfg = self.config
        return {
            "run_dir": str(self.run_dir),
            "repo_path": str(cfg.repo_path),
            "runtime_image": str(cfg.runtime_image),
            "runtime_binds": list(cfg.runtime_binds),
            "read_only_binds": list(cfg.read_only_binds),
            "goal": cfg.goal,
            "editable_paths": list(cfg.editable_paths),
            "frozen_paths": list(cfg.frozen_paths),
            "gate_block": cfg.gate_block,
            "proposal_slots": cfg.proposal_slots,
            "scientist_steps": cfg.scientist_steps,
            "agent_timeout_seconds": cfg.agent_timeout_seconds,
            "command_timeout_seconds": cfg.command_timeout_seconds,
            "command_output_cap_chars": cfg.command_output_cap_chars,
            "eval_timeout_seconds": cfg.eval_timeout_seconds,
            "researcher": dict(cfg.researcher),
            "context": dict(cfg.context),
            "prompt_dir": str(cfg.prompt_dir) if cfg.prompt_dir else "",
        }

    def _experiment_defaults(self) -> dict[str, Any]:
        """Config-derived payload fields the experiment worker needs."""
        cfg = self.config
        return {
            "run_dir": str(self.run_dir),
            "repo_path": str(cfg.repo_path),
            "runtime_image": str(cfg.runtime_image),
            "editable_paths": list(cfg.editable_paths),
            "frozen_paths": list(cfg.frozen_paths),
            "read_only_binds": list(cfg.read_only_binds),
            "eval_commands": list(cfg.eval_commands),
            "metrics_schema": dict(cfg.metrics_schema),
            "agent_timeout_seconds": cfg.agent_timeout_seconds,
            "eval_timeout_seconds": cfg.eval_timeout_seconds,
            "executor": dict(cfg.executor),
        }

    @abstractmethod
    def _launch(
        self,
        kind: str,
        work_id: str,
        request: WorkerRequest,
        manifest_path: Path,
        result_path: Path,
        argv: list[str],
    ) -> JobSpec:
        """Turn a staged manifest into a running worker (the backend seam)."""
