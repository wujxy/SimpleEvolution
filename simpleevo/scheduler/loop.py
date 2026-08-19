"""Scheduler event loop: allocate proposers, drain queue, ingest results."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import FrontierAxis, GateDecision, GateResult, ResearchStore
from simpleevo.db.queries import ResearchQueries

from .frontier import FrontierConfig, compute_frontier, sample_proposer_nodes
from .queue import ExecutorQueue, QueueConfig
from .reconcile import Reconciler
from .telemetry import TelemetryRecorder


@dataclass(frozen=True)
class SchedulerConfig:
    max_proposer_inflight: int = 2
    max_experiment_inflight: int = 2
    queue: QueueConfig | None = None
    frontier: FrontierConfig | None = None
    poll_seconds: float = 5.0
    quiescence_window_proposals: int = 2


class Scheduler:
    """Event-driven scheduler for the Research Tree."""

    def __init__(
        self,
        store: ResearchStore,
        run_dir: Path,
        config: SchedulerConfig,
        *,
        evolution_config: EvolutionConfig | None = None,
        submit_proposer: Callable[[str, dict[str, Any]], str] | None = None,
        submit_experiment: Callable[[str, dict[str, Any]], str] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.run_dir = Path(run_dir)
        self.config = config
        self.evolution_config = evolution_config
        self.submit_proposer = submit_proposer or (lambda _aid, _p: "")
        self.submit_experiment = submit_experiment or (lambda _eid, _p: "")
        self.clock = clock
        self._proposer_inflight: set[str] = set()
        self._experiment_inflight: set[str] = set()
        self._allocations_counter: dict[str, int] = {}
        self._queries = ResearchQueries(store.path)
        self._telemetry = TelemetryRecorder(self.run_dir)
        self._step_count = 0
        self._last_proposal_step = 0

    def step(self) -> dict[str, Any]:
        """Run one scheduler iteration.  Returns telemetry for the step."""
        self._step_count += 1

        # 1. Reconcile offline results.
        reconciler = Reconciler(self.store, self.run_dir)
        reconcile_actions = reconciler.reconcile()
        self._execute_reconcile_actions(reconcile_actions)

        # 2. Compute frontier.
        frontier = self._compute_frontier()

        # 3. Allocate proposers to frontier nodes.
        proposer_jobs = self._allocate_proposers(frontier)

        # 4. Poll proposer results and publish proposals.
        published = self._poll_proposers()
        if published:
            self._last_proposal_step = self._step_count

        # 5. Drain executor queue up to capacity.
        experiment_jobs = self._drain_executor_queue(frontier)

        # 6. Poll running experiments for completed results.
        ingested = self._poll_experiments()

        # 7. Record telemetry.
        self._telemetry.record(
            step=self._step_count,
            frontier_size=len(frontier.node_ids),
            queries=self._queries,
        )

        return {
            "frontier_size": len(frontier.node_ids),
            "proposer_jobs": len(proposer_jobs),
            "published": len(published),
            "experiment_jobs": len(experiment_jobs),
            "ingested": len(ingested),
            "reconcile_actions": len(reconcile_actions),
        }

    def run(self, *, max_steps: int | None = None) -> dict[str, Any]:
        """Run the scheduler until quiescence or max_steps."""
        telemetry: list[dict[str, Any]] = []
        while True:
            if max_steps is not None and self._step_count >= max_steps:
                break
            step_telemetry = self.step()
            telemetry.append(step_telemetry)
            if self._quiescent():
                break
            time.sleep(self.config.poll_seconds)
        return {"steps": self._step_count, "telemetry": telemetry}

    def _frontier_config(self) -> FrontierConfig:
        if self.config.frontier is not None:
            return self.config.frontier
        if self.evolution_config is not None:
            return FrontierConfig(
                axes=self.evolution_config.axes,
                schema=dict(self.evolution_config.metrics_schema),
            )
        return FrontierConfig(axes=())

    def _compute_frontier(self):
        config = self._frontier_config()
        nodes = self._queries.list_active_nodes()
        current_axes = self._load_frontier_axes()
        return compute_frontier(nodes, current_axes, config)

    def _load_frontier_axes(self) -> list[FrontierAxis]:
        with self.store.transaction() as tx:
            rows = tx._conn.execute("SELECT * FROM frontier_axes").fetchall()
            return [
                FrontierAxis(
                    axis=row["axis"],
                    node_id=row["node_id"],
                    value=row["value"],
                    margin=row["margin"],
                    hysteresis_anchor=row["hysteresis_anchor"],
                    since=row["since"],
                )
                for row in rows
            ]

    def _allocate_proposers(self, frontier):
        """Create proposer allocations and submit jobs for frontier nodes."""
        capacity = self.config.max_proposer_inflight - len(self._proposer_inflight)
        if capacity <= 0 or not frontier.node_ids:
            return []

        jobs = []
        for node_id in sample_proposer_nodes(
            frontier,
            self._allocations_counter,
            capacity,
        ):
            # Pick or create a thread for this node.
            thread_info = self._thread_for_node(node_id)
            if thread_info is None:
                continue
            thread_id, snapshot_ref = thread_info
            node = self._queries.get_node(node_id)
            if node is None:
                continue
            allocation = self.store.allocate_proposer(
                node_id=node_id, thread_id=thread_id
            )
            self._proposer_inflight.add(allocation.allocation_id)
            payload = {
                "allocation_id": allocation.allocation_id,
                "node_id": node_id,
                "node_sha": node.sha,
                "thread_id": thread_id,
                "snapshot_ref": snapshot_ref,
                "world_transition": {},
            }
            self.submit_proposer(allocation.allocation_id, payload)
            jobs.append(allocation.allocation_id)
        return jobs

    def _thread_for_node(self, node_id: str) -> tuple[str, str] | None:
        """Return (thread_id, snapshot_ref) for the node, creating a fresh thread if needed."""
        threads = self._queries.threads_for_node(node_id, limit=1)
        if threads:
            thread = threads[0]
            return thread.thread_id, thread.snapshot_ref
        # Fresh thread.
        with self.store.transaction() as tx:
            thread = tx.create_thread(
                parent_thread_id=None,
                node_id=node_id,
                snapshot_ref="",
            )
        return thread.thread_id, thread.snapshot_ref

    def _drain_executor_queue(self, frontier):
        """Submit queued proposals as experiment jobs up to capacity."""
        queue = ExecutorQueue(
            self.store,
            frontier.node_ids,
            self.config.queue or QueueConfig(),
        )
        queue.cleanup()
        capacity = self.config.max_experiment_inflight - len(self._experiment_inflight)
        if capacity <= 0:
            return []

        jobs = []
        for proposal_id in queue.dequeue(capacity):
            proposal = self.store.get_proposal(proposal_id)
            if proposal is None:
                continue
            node = self._queries.get_node(proposal.node_id)
            if node is None:
                continue
            with self.store.transaction() as tx:
                experiment = tx.create_experiment(
                    proposal_id=proposal_id,
                    parent_node_id=proposal.node_id,
                    status="pending",
                )
                tx.transition_proposal_status(proposal_id, "running")
            self._experiment_inflight.add(experiment.experiment_id)
            payload = {
                "experiment_id": experiment.experiment_id,
                "proposal_id": proposal_id,
                "parent_node_id": proposal.node_id,
                "parent_sha": node.sha,
                "proposal": proposal.instruction,
            }
            self.submit_experiment(experiment.experiment_id, payload)
            jobs.append(experiment.experiment_id)
        return jobs

    def _poll_proposers(self) -> list[str]:
        """Poll result files for running proposer allocations and publish proposals."""
        published: list[str] = []
        still_running: set[str] = set()
        for allocation_id in list(self._proposer_inflight):
            result_path = self.run_dir / "proposer_allocations" / allocation_id / "result.json"
            if not result_path.exists():
                still_running.add(allocation_id)
                continue
            if self._ingest_proposer_result(allocation_id, result_path):
                published.append(allocation_id)
            else:
                still_running.add(allocation_id)
        self._proposer_inflight = still_running
        return published

    def _poll_experiments(self) -> list[str]:
        """Poll result files for running experiments and ingest them."""
        ingested: list[str] = []
        still_running: set[str] = set()
        for eid in list(self._experiment_inflight):
            result_path = self.run_dir / "experiments" / eid / "result.json"
            if not result_path.exists():
                still_running.add(eid)
                continue
            if self._ingest_experiment_result(eid, result_path):
                ingested.append(eid)
            else:
                still_running.add(eid)
        self._experiment_inflight = still_running
        return ingested

    def _quiescent(self) -> bool:
        """True when there is nothing left to do."""
        if self._proposer_inflight or self._experiment_inflight:
            return False
        if self.store.queued_proposals():
            return False
        # Wait a few steps after the last published proposal before declaring
        # quiescence, so late-arriving results have a chance to be ingested.
        window = max(1, self.config.quiescence_window_proposals)
        if self._step_count - self._last_proposal_step < window:
            return False
        return True

    def _execute_reconcile_actions(
        self,
        actions: list,
    ) -> tuple[list[str], list[str]]:
        """Ingest results discovered by the reconciler."""
        published: list[str] = []
        ingested: list[str] = []
        for action in actions:
            if action.kind != "ingest_result":
                continue
            if action.work_kind == "proposer":
                result_path = (
                    self.run_dir
                    / "proposer_allocations"
                    / action.logical_work_id
                    / "result.json"
                )
                if self._ingest_proposer_result(
                    action.logical_work_id, result_path
                ):
                    published.append(action.logical_work_id)
                    # Remove from inflight if it was tracked there.
                    self._proposer_inflight.discard(action.logical_work_id)
            elif action.work_kind == "experiment":
                result_path = (
                    self.run_dir
                    / "experiments"
                    / action.logical_work_id
                    / "result.json"
                )
                if self._ingest_experiment_result(
                    action.logical_work_id, result_path
                ):
                    ingested.append(action.logical_work_id)
                    self._experiment_inflight.discard(action.logical_work_id)
        return published, ingested

    def _ingest_proposer_result(
        self,
        allocation_id: str,
        result_path: Path,
    ) -> bool:
        """Publish proposals from a completed proposer result file."""
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            result = raw.get("result", {})
            node_id = result.get("node_id")
            thread_id = result.get("thread_id")
            proposals = result.get("proposals", [])
            if node_id and thread_id and proposals:
                self.store.publish_proposals(
                    node_id=node_id,
                    thread_id=thread_id,
                    proposals=proposals,
                )
            self.store.deallocate_proposer(
                allocation_id=allocation_id,
                proposals_produced=len(proposals),
            )
            return True
        except Exception as exc:
            print(
                f"[scheduler] failed to ingest proposer {allocation_id}: {exc}",
                flush=True,
            )
            return False

    def _ingest_experiment_result(
        self,
        experiment_id: str,
        result_path: Path,
    ) -> bool:
        """Ingest a completed experiment result file."""
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            result = raw.get("result", {})
            gate_raw = result.get("gate", {})
            gate = GateDecision(
                results={
                    name: GateResult(g.get("passed"), g.get("detail", ""))
                    for name, g in gate_raw.get("results", {}).items()
                },
                passed=gate_raw.get("passed", False),
            )
            status = str(result.get("status", "completed")).lower()
            self.store.ingest_experiment_result(
                experiment_id=experiment_id,
                result_sha=result.get("sha"),
                metrics=result.get("metrics", {}),
                gate_result=gate,
                status=status,
                frontier_config=self._frontier_config(),
            )
            return True
        except Exception as exc:
            print(
                f"[scheduler] failed to ingest experiment {experiment_id}: {exc}",
                flush=True,
            )
            return False
