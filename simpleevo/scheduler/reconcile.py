"""Resume / reconciliation logic for the Scheduler.

Reconciliation aligns L2 Research State with durable artifacts and the external
job scheduler after a crash or restart.  It is deliberately conservative: it
reattaches running jobs, ingests completed results, and creates new Attempts for
lost infrastructure failures.  It never invents scientific results.

The ``submitter`` hook is what makes this backend-aware.  A local submitter
cannot observe its subprocesses, so missing-result work is simply re-submitted
when no attempt is running (Local's ``mark_running_attempts_lost`` on startup
makes that the right call).  A condor submitter *can* observe its jobs, so the
reconciler asks ``submitter.probe_job`` whether a missing-result job is still
alive: if it HELD or vanished, the running attempt is marked infra-failed and
the normal retry machinery re-submits it (§18) — without this, a held/lost
condor job would leave the run hung forever on a phantom ``running`` attempt.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReconcileAction:
    kind: str
    logical_work_id: str
    work_kind: str = ""
    attempt_id: str | None = None
    reason: str = ""


class Reconciler:
    """Reconcile L2 non-terminal work with artifact store and job scheduler."""

    def __init__(
        self,
        store,
        artifact_root: Path,
        *,
        submitter=None,
    ):
        self.store = store
        self.artifact_root = Path(artifact_root)
        self.submitter = submitter

    def reconcile(self) -> list[ReconcileAction]:
        """Return actions to bring L2 in line with reality."""
        actions: list[ReconcileAction] = []
        actions.extend(self._reconcile_proposers())
        actions.extend(self._reconcile_experiments())
        actions.extend(self._reconcile_supervisor())
        return actions

    def _reconcile_supervisor(self) -> list[ReconcileAction]:
        actions = []
        for attempt in self.store.running_attempts("supervisor"):
            result_path = (
                self.artifact_root / "supervisor_decisions"
                / attempt.logical_work_id / "result.json"
            )
            if result_path.exists():
                actions.append(ReconcileAction(
                    kind="ingest_result",
                    logical_work_id=attempt.logical_work_id,
                    work_kind="supervisor",
                    reason=f"supervisor result file exists: {result_path}",
                ))
            else:
                actions.append(self._wait_or_retry(
                    attempt.logical_work_id, "supervisor",
                ))
        return actions

    def _reconcile_proposers(self) -> list[ReconcileAction]:
        actions: list[ReconcileAction] = []
        with self.store.transaction() as tx:
            rows = tx._conn.execute(
                """
                SELECT allocation_id FROM proposer_allocations
                WHERE finished_at IS NULL
                """
            ).fetchall()
            for row in rows:
                allocation_id = row["allocation_id"]
                result_path = (
                    self.artifact_root
                    / "proposer_allocations"
                    / allocation_id
                    / "result.json"
                )
                if result_path.exists():
                    actions.append(ReconcileAction(
                        kind="ingest_result",
                        logical_work_id=allocation_id,
                        work_kind="proposer",
                        reason=f"proposer result file exists: {result_path}",
                    ))
                else:
                    actions.append(self._wait_or_retry(allocation_id, "proposer"))
        return actions

    def _reconcile_experiments(self) -> list[ReconcileAction]:
        actions: list[ReconcileAction] = []
        with self.store.transaction() as tx:
            rows = tx._conn.execute(
                "SELECT experiment_id FROM experiments WHERE status IN (?, ?)",
                ("pending", "running"),
            ).fetchall()
            for row in rows:
                eid = row["experiment_id"]
                result_path = self.artifact_root / "experiments" / eid / "result.json"
                if result_path.exists():
                    actions.append(ReconcileAction(
                        kind="ingest_result",
                        logical_work_id=eid,
                        work_kind="experiment",
                        reason=f"experiment result file exists: {result_path}",
                    ))
                else:
                    actions.append(self._wait_or_retry(eid, "experiment"))
        return actions

    # ------------------------------------------------------------------
    # Backend-aware wait-vs-retry
    # ------------------------------------------------------------------

    def _wait_or_retry(self, work_id: str, work_kind: str) -> ReconcileAction:
        """Missing-result work: wait, or mark dead if the cluster says so.

        The default (no submitter, or probe unknown/running) is to wait — the
        ``reattach_or_wait`` action re-submits only when no attempt is running.
        When the cluster reports the job HELD or gone (past its disappearance
        grace), the running attempt is infra-failed first so the retry path
        actually triggers instead of waiting on a phantom ``running`` attempt.
        """
        probe = self._probe(work_id, work_kind)
        if probe in ("held", "gone"):
            if self.submitter is not None:
                self.submitter.remove_job(work_id, work_kind)
            self._mark_infra_failed(work_id, work_kind)
            return ReconcileAction(
                kind="reattach_or_wait",
                logical_work_id=work_id,
                work_kind=work_kind,
                reason=f"{work_kind} job {probe}: marked infra-failed for retry",
            )
        return ReconcileAction(
            kind="reattach_or_wait",
            logical_work_id=work_id,
            work_kind=work_kind,
            reason="result file not yet present",
        )

    def _probe(self, work_id: str, work_kind: str) -> str:
        if self.submitter is None:
            return "unknown"
        try:
            return self.submitter.probe_job(work_id, work_kind)
        except Exception as exc:  # a broken probe must never look like 'gone'
            print(f"[reconcile] probe_job({work_kind} {work_id}) failed: {exc}",
                  flush=True)
            return "unknown"

    def _mark_infra_failed(self, work_id: str, work_kind: str) -> None:
        """Mark the running attempt infra-failed so it becomes re-submittable.

        A dead job with no recorded running attempt is already re-submittable
        (reattach_or_wait re-submits when nothing is running), so this only acts
        when a running attempt exists."""
        attempts = self.store.attempts_for_work(work_id, work_kind)
        running = [a for a in attempts if a.status == "running"]
        if not running:
            return
        attempt = running[-1]
        if work_kind == "proposer":
            self.store.mark_proposer_infra_failed(
                allocation_id=work_id, attempt_id=attempt.attempt_id)
        elif work_kind == "experiment":
            self.store.mark_experiment_infra_failed(
                experiment_id=work_id, attempt_id=attempt.attempt_id)
        else:
            self.store.mark_attempt_failed(attempt.attempt_id)
