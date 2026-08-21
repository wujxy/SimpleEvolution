"""Local subprocess scheduler adapter for SimpleEvolution.

This is the default job backend for smoke tests and local development.  It
writes a ``WorkerRequest`` manifest for each job and spawns the appropriate
worker module as a subprocess.  HTCondor deployments use
``simpleevo.jobs.condor.HTCondorSubmitter`` instead — same interface, same
manifest/result path conventions, different ``_launch``.

Both backends share ``BaseSubmitter``; the only thing Local implements is the
``_launch`` seam (subprocess.Popen) plus the startup semantic that local
subprocesses die with their parent scheduler.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import EvolutionConfig
from .base import BaseSubmitter, JobSpec
from .envelope import WorkerRequest
from .job_env import worker_environment


class LocalSubmitter(BaseSubmitter):
    """Launch proposer/experiment workers as local subprocesses.

    Implements the callable interface the Scheduler injects for
    ``submit_proposer`` and ``submit_experiment`` — identical in shape to
    ``HTCondorSubmitter``.
    """

    def __init__(
        self,
        run_dir: str | Path,
        config: EvolutionConfig,
        *,
        python: str | None = None,
    ):
        super().__init__(run_dir, config, python=python)

    def _launch(
        self,
        kind: str,
        work_id: str,
        request: WorkerRequest,
        manifest_path: Path,
        result_path: Path,
        argv: list[str],
    ) -> JobSpec:
        """Write a worker log and spawn the worker as a subprocess.

        The worker inherits the full host environment plus the package path
        and forwarded payload env (see ``worker_environment``), so it is
        importable regardless of the scheduler's own working directory.
        stdout/stderr are captured to ``worker.log`` so a silent crash is
        diagnosable (the Scheduler only polls ``result.json``).
        """
        log_file = open(manifest_path.parent / "worker.log", "ab")
        try:
            subprocess.Popen(
                [self.python, *argv],
                stdout=log_file,
                stderr=log_file,
                env=worker_environment(),
            )
        finally:
            log_file.close()
        return JobSpec(
            request=request,
            manifest_path=manifest_path,
            result_path=result_path,
            argv=argv,
            kind=kind,
            work_id=work_id,
        )
