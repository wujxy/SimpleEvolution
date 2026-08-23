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

import os
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
        # Live per-experiment benchmark-core leases: {work_id: (pin, Popen)}.
        # Only touched when BENCH_PIN is set in the launching environment.
        self._bench_leases: dict[str, tuple[int, subprocess.Popen]] = {}

    def _bench_pin_for(self, work_id: str) -> int | None:
        """A benchmark core this experiment gets exclusively while it lives.

        bench.sh pins every timed rep to one logical core (BENCH_PIN), so with
        ``max_experiment_inflight > 1`` concurrent evals would time-share that
        core and depress each other's measured rate.  Lease a distinct core
        from a small pool above the configured base pin: base first, then
        base+1 .. base+spread-1 (spread = max_experiment_inflight + 2 slack).
        An inflight=1 run therefore always lands on base — identical to the
        unmodified behaviour — while a tree run's concurrent evals never
        share.  Leases are reclaimed lazily at allocation time: a worker
        releases its core the moment its process exits (success or crash).
        """
        base = self._bench_base_pin()
        if base is None:
            return None
        self._reclaim_bench_leases()
        busy = {pin for pin, _ in self._bench_leases.values()}
        for pin in self._bench_pin_pool(base):
            if pin not in busy:
                return pin
        print(
            f"warning: bench pin pool exhausted ({len(busy)} live experiments); "
            f"{work_id} reuses pin {base} — concurrent evals may contend",
            flush=True,
        )
        return base

    def _bench_base_pin(self) -> int | None:
        try:
            return int(os.environ.get("BENCH_PIN", ""))
        except (TypeError, ValueError):
            return None

    def _bench_pin_pool(self, base: int) -> list[int]:
        inflight = int(getattr(self.config, "max_experiment_inflight", 1) or 1)
        spread = max(inflight + 2, 4)
        n_cpus = os.cpu_count() or 128
        return [base + i for i in range(spread) if base + i < n_cpus]

    def _reclaim_bench_leases(self) -> None:
        for work_id in list(self._bench_leases):
            _pin, proc = self._bench_leases[work_id]
            if proc.poll() is not None:
                del self._bench_leases[work_id]

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
        env = worker_environment()
        proc_pin: int | None = None
        if kind == "experiment" and "BENCH_PIN" in env:
            # One exclusive benchmark core per live experiment (no-op for an
            # inflight=1 run: base is always free, so it always wins).
            proc_pin = self._bench_pin_for(work_id)
            if proc_pin is not None:
                env["BENCH_PIN"] = str(proc_pin)
        log_file = open(manifest_path.parent / "worker.log", "ab")
        try:
            proc = subprocess.Popen(
                [self.python, *argv],
                stdout=log_file,
                stderr=log_file,
                env=env,
            )
        finally:
            log_file.close()
        if proc_pin is not None:
            self._bench_leases[work_id] = (proc_pin, proc)
        return JobSpec(
            request=request,
            manifest_path=manifest_path,
            result_path=result_path,
            argv=argv,
            kind=kind,
            work_id=work_id,
        )
