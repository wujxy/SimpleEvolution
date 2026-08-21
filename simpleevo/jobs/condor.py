"""HTCondor scheduler adapter for SimpleEvolution.

``HTCondorSubmitter`` is the cluster twin of ``LocalSubmitter``: it stages the
same ``WorkerRequest`` manifest at the same ``run_dir/experiments/<id>/``
layout, then submits a vanilla condor job (``job.sh`` + ``job.sub``) instead of
spawning a subprocess.  The Scheduler's contract is unchanged — submit returns
the result path, the worker writes ``result.json`` there, the Scheduler polls
that file.

What the cluster backend adds on top of the shared contract:

- ``job_env.sh`` — a run-scoped export of the worker env (PYTHONPATH to the
  simpleevo packages + forwarded API-key/proxy vars).  ``job.sh`` sources it,
  so a job running on a different execute node gets the same environment a
  local subprocess would (see ``simpleevo.jobs.job_env``).
- A durable in-run ledger (``run_dir/jobs.json``) mapping logical work id ->
  condor ``cluster.proc``.  Because condor jobs outlive the scheduler
  (``presumes_dead_on_startup = False``), the ledger is what lets the
  Reconciler, after a scheduler restart, distinguish "job still running"
  (wait) from "job left the queue" (mark infra-failed and retry) instead of
  blindly re-submitting and doubling the load.
- ``probe_job`` — live ``condor_q`` state (idle/running/held/gone) consumed by
  the Reconciler.

Job lifecycle constants match SimpleLoop's ``hepjob.py`` (which this adapter
mirrors): condor JobStatus 1=Idle, 2=Running, 5=Held; IHEP requires
``+IHEP_RealGroup`` even when ``accounting_group`` is set (derived from its
``<ORG>.<group>.<...>`` form unless ``ihep_group`` is given explicitly).
"""
from __future__ import annotations

import getpass
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..config import EvolutionConfig, JobConfig
from .base import BaseSubmitter, JobSpec
from .envelope import WorkerRequest
from .job_env import write_job_env

# condor JobStatus codes (from a successful `condor_q -af JobStatus`)
_JOB_IDLE = 1
_JOB_RUNNING = 2
_JOB_HELD = 5

# cpu_model short-name -> Requirements clause (IHEP CpuFamily/CpuModelNumber).
_CPU_MODEL_REQUIREMENTS = {
    "zen4": "CpuFamily==25 && CpuModelNumber==17",
    "zen5": "CpuFamily==26 && CpuModelNumber==2",
    "icelake": "CpuFamily==6 && CpuModelNumber==106",
    "skylake": "CpuFamily==6 && CpuModelNumber==85",
    "skylake-x": "CpuFamily==6 && CpuModelNumber==79",
}


class HTCondorSubmitter(BaseSubmitter):
    """Submit proposer/experiment workers as vanilla condor jobs."""

    backend = "condor"
    presumes_dead_on_startup = False  # condor jobs outlive the scheduler

    def __init__(
        self,
        run_dir: str | Path,
        config: EvolutionConfig,
        *,
        python: str | None = None,
        job_cfg: JobConfig | None = None,
    ):
        # Explicit arg > job config's python_executable > submit host's
        # interpreter (which must live on shared Lustre so execute nodes can
        # reach it).
        self.job_cfg = job_cfg or config.jobs
        if python is None:
            python = self.job_cfg.python_executable or sys.executable
        super().__init__(run_dir, config, python=python)
        # The ledger is rebuilt from the run dir on every submitter instance so
        # resume picks up jobs submitted by a previous process (§18).
        self._ledger_path = self.run_dir / "jobs.json"
        self._ledger = self._load_ledger()
        self._ensure_job_env()

    # ------------------------------------------------------------------
    # condor wrappers (the only places that touch condor_*)
    # ------------------------------------------------------------------

    def _target_args(self) -> list[str]:
        """condor -pool/-name flags selecting the target schedd.

        -pool is required on login nodes whose default collector cannot see the
        JUNO schedds (cm01.ihep.ac.cn owns schedd06/07/10/11/12)."""
        args: list[str] = []
        if self.job_cfg.collector:
            args += ["-pool", self.job_cfg.collector]
        if self.job_cfg.schedd_name:
            args += ["-name", self.job_cfg.schedd_name]
        return args

    def _requirements_expr(self) -> str | None:
        """Requirements from cpu_model and/or machine_constraint (AND-joined)."""
        parts: list[str] = []
        if self.job_cfg.cpu_model:
            expr = _CPU_MODEL_REQUIREMENTS.get(self.job_cfg.cpu_model)
            if expr:
                parts.append(expr)
        if self.job_cfg.machine_constraint:
            parts.append(self.job_cfg.machine_constraint)
        return " && ".join(parts) if parts else None

    def _query_statuses(self, job_ids: list[str]) -> dict[str, int] | None:
        """{job_id: JobStatus} for the given jobs, or None when the query itself
        failed (callers must not treat that as 'job disappeared')."""
        if not job_ids:
            return {}
        argv = [self.job_cfg.query_cmd] + self._target_args()
        argv += ["-af", "ClusterId", "ProcId", "JobStatus"]
        completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            return None
        wanted = set(job_ids)
        statuses: dict[str, int] = {}
        for line in completed.stdout.splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            job_id = f"{parts[0]}.{parts[1]}"
            if job_id not in wanted:
                continue
            try:
                statuses[job_id] = int(parts[2])
            except ValueError:
                continue
        return statuses

    def _hold_reason(self, job_id: str) -> str:
        cluster, _, proc = job_id.partition(".")
        argv = [self.job_cfg.query_cmd] + self._target_args()
        argv += ["-af", "HoldReason",
                 "-constraint", f"ClusterId=={cluster} && ProcId=={proc}"]
        completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        reason = completed.stdout.strip()
        return reason if completed.returncode == 0 and reason else "unknown"

    # ------------------------------------------------------------------
    # job_env (run-scoped worker environment)
    # ------------------------------------------------------------------

    def _job_env_path(self) -> Path:
        return self.run_dir / "job_env.sh"

    def _ensure_job_env(self) -> Path:
        """Render run_dir/job_env.sh, overlaying the configured cluster proxy.

        A configured ``jobs.http_proxy`` / ``https_proxy`` / ``no_proxy`` is
        authoritative for condor jobs (independent of the submit host's own
        env), so execute nodes route external model/API traffic through the
        jump host. With nothing configured the behaviour is unchanged: the
        submit host's forwarded env is written as-is.
        """
        env = os.environ.copy()
        env.update(self.job_cfg.proxy_env())
        return write_job_env(self._job_env_path(), env)

    # ------------------------------------------------------------------
    # backend seam
    # ------------------------------------------------------------------

    def _launch(
        self,
        kind: str,
        work_id: str,
        request: WorkerRequest,
        manifest_path: Path,
        result_path: Path,
        argv: list[str],
    ) -> JobSpec:
        """Write job.sh + job.sub and condor_submit the worker."""
        job_dir = manifest_path.parent
        job_sh = job_dir / "job.sh"
        job_sh.write_text(
            "#!/usr/bin/env bash\n"
            "set -uo pipefail\n"
            f"source {shlex.quote(str(self._job_env_path()))}\n"
            f"exec {shlex.quote(self.python)}"
            f" {' '.join(shlex.quote(a) for a in argv)} --job-id \"$1\"\n",
            encoding="utf-8")
        job_sh.chmod(0o755)

        lines = [
            "universe = vanilla",
            f"executable = {job_sh}",
            'arguments = "$(ClusterId).$(ProcId)"',
            f"output = {job_dir / 'job.out'}",
            f"error = {job_dir / 'job.err'}",
            f"log = {job_dir / 'job.log'}",
            "should_transfer_files = NO",
            f"request_memory = {self.job_cfg.memory_mb}",
            f"request_cpus = {self.job_cfg.cpus}",
            f"accounting_group = {self.job_cfg.accounting_group}",
            f"accounting_group_user = {self.job_cfg.accounting_group_user or getpass.getuser()}",
            f'+HepJob_RequestOS = "{self.job_cfg.request_os}"',
        ]
        req = self._requirements_expr()
        if req:
            lines.append(f"Requirements = {req}")
        if self.job_cfg.ihep_group:
            lines.append(f'+IHEP_RealGroup = "{self.job_cfg.ihep_group}"')
        else:
            parts = self.job_cfg.accounting_group.split(".")
            if len(parts) >= 2 and parts[0] and parts[1]:
                lines.append(f'+IHEP_RealGroup = "{parts[1]}"')
        lines.append("queue")
        submit_file = job_dir / "job.sub"
        submit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        argv_cmd = [self.job_cfg.submit_cmd] + self._target_args()
        argv_cmd.append(str(submit_file))
        completed = subprocess.run(argv_cmd, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"condor submit failed for {kind} {work_id}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}")
        match = re.search(r"submitted to cluster (\d+)", completed.stdout)
        if not match:
            raise RuntimeError(
                f"could not parse cluster id from submit output: "
                f"{completed.stdout.strip()[:400]}")
        job_id = f"{match.group(1)}.0"
        self._record_job(kind, work_id, job_id)
        print(f"[{time.strftime('%H:%M:%S')}] {kind} {work_id} submitted "
              f"as condor job {job_id}", flush=True)
        return JobSpec(
            request=request,
            manifest_path=manifest_path,
            result_path=result_path,
            argv=argv,
            kind=kind,
            work_id=work_id,
            job_id=job_id,
        )

    # ------------------------------------------------------------------
    # live probing (Reconciler integration)
    # ------------------------------------------------------------------

    def probe_job(self, logical_work_id: str, work_kind: str) -> str:
        """Report ``running | held | gone | unknown`` for a ledger job."""
        entry = self._ledger.get(work_kind, {}).get(logical_work_id)
        job_id = (entry or {}).get("job_id")
        if not job_id:
            return "unknown"
        statuses = self._query_statuses([job_id])
        if statuses is None:
            return "unknown"  # transient query failure — never treat as gone
        status = statuses.get(job_id)
        if status in (_JOB_IDLE, _JOB_RUNNING):
            if entry.get("gone_since") is not None:
                entry["gone_since"] = None
                self._save_ledger()
            return "running"
        if status == _JOB_HELD:
            return "held"
        if status is None:
            # Left the queue without a result.  Give it a grace window before
            # declaring it gone, so a job whose result just landed on another
            # schedd doesn't get re-submitted in a duplicate.
            now = time.time()
            if entry.get("gone_since") is None:
                entry["gone_since"] = now
                self._save_ledger()
                return "unknown"
            if now - entry["gone_since"] > self.job_cfg.disappearance_grace_seconds:
                return "gone"
            return "unknown"
        return "unknown"

    def remove_job(self, logical_work_id: str, work_kind: str) -> None:
        """condor_rm the ledger job and drop its ledger entry (best-effort)."""
        entry = self._ledger.get(work_kind, {}).get(logical_work_id)
        if entry and entry.get("job_id"):
            subprocess.run(
                [self.job_cfg.remove_cmd] + self._target_args()
                + [entry["job_id"]],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
        self._ledger.setdefault(work_kind, {}).pop(logical_work_id, None)
        self._save_ledger()

    # ------------------------------------------------------------------
    # ledger (run_dir/jobs.json: work id -> condor job id)
    # ------------------------------------------------------------------

    def _load_ledger(self) -> dict[str, dict]:
        try:
            raw = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _save_ledger(self) -> None:
        tmp = self._ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._ledger, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
        os.replace(tmp, self._ledger_path)

    def _record_job(self, kind: str, work_id: str, job_id: str) -> None:
        self._ledger.setdefault(kind, {})[work_id] = {
            "job_id": job_id,
            "submitted_at": time.time(),
            "gone_since": None,
        }
        self._save_ledger()
