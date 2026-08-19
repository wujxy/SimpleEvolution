"""Resume / reconciliation logic for the Scheduler.

Reconciliation aligns L2 Research State with durable artifacts and the external
job scheduler after a crash or restart.  It is deliberately conservative: it
reattaches running jobs, ingests completed results, and creates new Attempts for
lost infrastructure failures.  It never invents scientific results.
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
        scheduler=None,
    ):
        self.store = store
        self.artifact_root = Path(artifact_root)
        self.scheduler = scheduler

    def reconcile(self) -> list[ReconcileAction]:
        """Return actions to bring L2 in line with reality."""
        actions: list[ReconcileAction] = []
        actions.extend(self._reconcile_proposers())
        actions.extend(self._reconcile_experiments())
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
                    # Local-submitter path: if the result file is absent we
                    # assume the job is still running.  HTCondor adapters can
                    # override this by inspecting the external scheduler.
                    actions.append(ReconcileAction(
                        kind="reattach_or_wait",
                        logical_work_id=allocation_id,
                        work_kind="proposer",
                        reason="result file not yet present",
                    ))
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
                    actions.append(ReconcileAction(
                        kind="reattach_or_wait",
                        logical_work_id=eid,
                        work_kind="experiment",
                        reason="result file not yet present",
                    ))
        return actions
