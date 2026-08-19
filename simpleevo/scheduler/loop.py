"""Scheduler event loop: allocate proposers, drain queue, ingest results."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import FrontierAxis, GateDecision, GateResult, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator, load_generator_basis, sample_generators

from .frontier import (
    FrontierConfig,
    build_policy,
    compute_frontier,
    sample_proposer_nodes,
)
from .queue import ExecutorQueue, QueueConfig
from .reconcile import Reconciler
from .telemetry import TelemetryRecorder


@dataclass(frozen=True)
class SchedulerConfig:
    max_proposer_inflight: int = 2
    max_experiment_inflight: int = 2
    proposal_slots: int = 3
    queue: QueueConfig | None = None
    frontier: FrontierConfig | None = None
    poll_seconds: float = 5.0
    quiescence_window_proposals: int = 2


# Scientific terminal outcomes produced by the experiment worker.  These are
# the only values that may reach ``experiments.status`` (§16/§17): anything
# else (executor crash, worker crash, network/API failure) is infrastructure
# and lands on the Attempt table instead.
_EXPERIMENT_SCI_STATUS = {
    "COMPLETED": "completed",
    "GATE_REJECTED": "gate_rejected",
    "NO_CHANGE": "no_change",
}


class Scheduler:
    """Event-driven scheduler for the Research Tree.

    In-flight work is derived from L2, never from process memory: proposer
    allocations (``finished_at IS NULL``) and experiments (``pending``/
    ``running``) plus their ``attempts`` rows are the single source of truth.
    Restart = reconciliation, not recovery of the old process (§18).
    """

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
        generator_basis: list[Generator] | None = None,
    ):
        self.store = store
        self.run_dir = Path(run_dir)
        self.config = config
        self.evolution_config = evolution_config
        self.submit_proposer = submit_proposer or (lambda _aid, _p: "")
        self.submit_experiment = submit_experiment or (lambda _eid, _p: "")
        self.clock = clock
        self._allocations_counter: dict[str, int] = {}
        self._queries = ResearchQueries(store.path)
        self._telemetry = TelemetryRecorder(self.run_dir)
        self._step_count = 0
        self._last_proposal_step = 0
        # Injected basis wins; otherwise the repo-root generator.json is loaded
        # lazily on first need and cached.
        self._generator_basis: list[Generator] | None = generator_basis

        # Local subprocesses do not survive their parent; anything left
        # ``running`` by a previous process is dead and re-submittable (§18).
        self.store.mark_running_attempts_lost()

    def step(self) -> dict[str, Any]:
        """Run one scheduler iteration.  Returns telemetry for the step."""
        self._step_count += 1

        # 1. Reconcile offline results + re-submit dead work.
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

    # ------------------------------------------------------------------
    # Frontier
    # ------------------------------------------------------------------

    def _frontier_config(self) -> FrontierConfig:
        if self.config.frontier is not None:
            return self.config.frontier
        if self.evolution_config is not None:
            return FrontierConfig(
                axes=self.evolution_config.axes,
                schema=dict(self.evolution_config.metrics_schema),
                policy=build_policy(
                    self.evolution_config.frontier_policy,
                    top_k=self.evolution_config.frontier_top_k,
                ),
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

    # ------------------------------------------------------------------
    # Proposer allocation
    # ------------------------------------------------------------------

    def _proposer_capacity(self) -> int:
        return self.config.max_proposer_inflight - self.store.count_running_attempts("proposer")

    def _allocate_proposers(self, frontier):
        """Create proposer allocations and submit jobs for frontier nodes."""
        capacity = self._proposer_capacity()
        if capacity <= 0 or not frontier.node_ids:
            return []

        jobs = []
        for node_id in sample_proposer_nodes(
            frontier,
            self._allocations_counter,
            capacity,
            self._frontier_config(),
        ):
            episode = self._idle_episode_for_node(node_id)
            if episode is None:
                # Frontier node whose episodes are all terminal: re-study it
                # by reseeding a fresh episode (GEPA pool semantics), bounded
                # by max_research_per_node. Skip when the budget is spent.
                episode = self._reseed_episode(node_id)
                if episode is None:
                    continue
            node = self._queries.get_node(node_id)
            if node is None:
                continue
            allocation = self.store.allocate_proposer(
                node_id=node_id,
                episode_id=episode.episode_id,
                proposal_slots=self.config.proposal_slots,
            )
            attempt = self.store.record_attempt(
                logical_work_id=allocation.allocation_id,
                kind="proposer",
                status="running",
                started_at=self.clock(),
            )
            ordinal = len(self.store.attempts_for_work(
                allocation.allocation_id, "proposer"))
            self.submit_proposer(
                allocation.allocation_id,
                self._proposer_payload(
                    allocation, node, episode, attempt.attempt_id, ordinal),
            )
            jobs.append(allocation.allocation_id)
        return jobs

    def _idle_episode_for_node(self, node_id: str):
        """Return a FRESH episode for ``node_id`` that has never run.

        A Scientist Episode is single-use (§3.4): one episode = one research
        act = one final cognition.  An episode that has ever had an allocation
        (in-flight OR completed) is terminal and must never be re-scheduled;
        children get their own fresh episode.  A node has one fresh episode by
        default; single-use prevents that episode from being re-picked after
        it ends (which would otherwise overwrite its session and corrupt child
        inheritance).
        """
        allocated = self.store.allocated_episode_ids()
        episodes = self._queries.episodes_for_node(node_id, limit=1000)
        for episode in episodes:
            if episode.episode_id not in allocated:
                return episode
        return None

    def _reseed_episode(self, node_id: str):
        """Fresh episode for a frontier node whose episodes are all terminal.

        Mirrors the manual ``reseed`` CLI (cli.py:_cmd_reseed) but records
        inheritance: the new episode's ``inherited_from_episode_id`` is the
        node's most recent episode, so the forked Scientist resumes the prior
        final cognition. When ``generator_reseed`` is on, the episode also
        carries a sampled variation operator (``variation_operator``) drawn
        from the generator basis — re-framing directives not yet tried on this
        node, so each re-study is pointed at a fresh cognitive axis. Bounded by
        ``max_research_per_node`` (total lifetime proposer allocations).
        Returns the fresh Episode, or None when the node is at its research
        budget.
        """
        budget = self._max_research_per_node()
        if self.store.count_allocations_for_node(node_id) >= budget:
            return None
        episodes = self._queries.episodes_for_node(node_id, limit=1000)
        most_recent = episodes[0] if episodes else None  # last_active_at DESC
        if most_recent is None:
            return None
        if not self.store.episode_allocation_finished(most_recent.episode_id):
            # Previous scientist is still in flight: no frozen final cognition
            # to inherit yet, so this node cannot be re-studied right now.
            return None
        variation_operator = self._variation_for(node_id, episodes)
        with self.store.transaction() as tx:
            return tx.create_episode(
                node_id=node_id,
                inherited_from_episode_id=most_recent.episode_id,
                variation_operator=variation_operator,
            )

    def _variation_for(self, node_id: str, episodes) -> str | None:
        """Sample untried generators for a re-study, as ``"G5+G3"`` ids.

        ``generator_reseed`` off, an empty basis, or no untried generators
        remaining all degrade to ``None`` (no variation factor — the reseed
        runs exactly as before).
        """
        if self.evolution_config is None or not self.evolution_config.generator_reseed:
            return None
        basis = self._generator_basis_or_load()
        if not basis:
            return None
        tried: set[str] = set()
        for episode in episodes:
            if episode.variation_operator:
                tried.update(episode.variation_operator.split("+"))
        chosen = sample_generators(basis, tried)
        if not chosen:
            return None
        return "+".join(g.id for g in chosen)

    def _generator_basis_or_load(self) -> list[Generator]:
        if self._generator_basis is None:
            self._generator_basis = load_generator_basis()
        return self._generator_basis

    def _max_research_per_node(self) -> int:
        if self.evolution_config is not None:
            return self.evolution_config.max_research_per_node
        return 3

    def _proposer_payload(
        self, allocation, node, episode, attempt_id: str, attempt: int,
    ) -> dict[str, Any]:
        """Assemble the proposer job payload from L2 (also used on re-submit)."""
        return {
            "allocation_id": allocation.allocation_id,
            "node_id": node.node_id,
            "node_sha": node.sha,
            "episode_id": episode.episode_id,
            "inherited_from_episode_id": episode.inherited_from_episode_id,
            "variation_operators": self._variation_operators_payload(episode),
            "proposal_ids": list(allocation.reserved_proposal_ids),
            "world_transition": self._world_transition_for(node),
            "attempt_id": attempt_id,
            "attempt": attempt,
        }

    def _variation_operators_payload(self, episode) -> list[dict[str, str]]:
        """Resolve the episode's stored generator ids to full directives.

        The proposer worker may not have access to ``generator.json``, so the
        resolved ``{id, name, description}`` triples travel in the payload; the
        worker's cli formats them into prompt text (see proposer/cli.py).
        """
        if not episode.variation_operator:
            return []
        basis = self._generator_basis_or_load()
        by_id = {g.id: g for g in basis}
        directives: list[dict[str, str]] = []
        for generator_id in episode.variation_operator.split("+"):
            generator = by_id.get(generator_id)
            if generator is None:
                continue
            directives.append(
                {
                    "id": generator.id,
                    "name": generator.name,
                    "description": generator.description,
                }
            )
        return directives

    def _world_transition_for(self, node) -> dict[str, Any]:
        """Assemble the reality record a child Scientist sees on resume (§8).

        ``node.experiment_id`` is the Experiment that produced this Node; its
        facts (metrics / gate / diff) are the world transition from the parent
        the forked Scientist last saw.
        """
        if node.experiment_id is None:
            return {}
        experiment = self._queries.get_experiment(node.experiment_id)
        if experiment is None:
            return {}
        parent = self._queries.get_node(experiment.parent_node_id)
        return {
            "parent_node_id": experiment.parent_node_id,
            "experiment_id": experiment.experiment_id,
            "metrics": dict(experiment.metrics),
            "gate": {
                "passed": experiment.gate_result.passed,
                "results": {
                    name: {"passed": gr.passed, "detail": gr.detail}
                    for name, gr in experiment.gate_result.results.items()
                },
            },
            "diff": list(experiment.changed_paths),
            "parent_metrics": dict(parent.metrics) if parent else {},
        }

    # ------------------------------------------------------------------
    # Executor queue
    # ------------------------------------------------------------------

    def _experiment_capacity(self) -> int:
        return self.config.max_experiment_inflight - self.store.count_running_attempts("experiment")

    def _drain_executor_queue(self, frontier):
        """Submit queued proposals as experiment jobs up to capacity."""
        queue = ExecutorQueue(
            self.store,
            frontier.node_ids,
            self.config.queue or QueueConfig(),
        )
        queue.cleanup()
        queue.enforce_bound()
        capacity = self._experiment_capacity()
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
            attempt = self.store.record_attempt(
                logical_work_id=experiment.experiment_id,
                kind="experiment",
                status="running",
                started_at=self.clock(),
            )
            ordinal = len(self.store.attempts_for_work(
                experiment.experiment_id, "experiment"))
            self.store.mark_experiment_running(experiment.experiment_id)
            self.submit_experiment(
                experiment.experiment_id,
                {
                    "experiment_id": experiment.experiment_id,
                    "proposal_id": proposal_id,
                    "parent_node_id": proposal.node_id,
                    "parent_sha": node.sha,
                    "proposal": proposal.instruction,
                    "attempt_id": attempt.attempt_id,
                    "attempt": ordinal,
                },
            )
            jobs.append(experiment.experiment_id)
        return jobs

    # ------------------------------------------------------------------
    # Polling + ingest
    # ------------------------------------------------------------------

    def _poll_proposers(self) -> list[str]:
        """Poll result files for running proposer attempts and publish proposals."""
        published: list[str] = []
        for attempt in self.store.running_attempts("proposer"):
            allocation_id = attempt.logical_work_id
            result_path = self.run_dir / "proposer_allocations" / allocation_id / "result.json"
            if not result_path.exists():
                continue
            if self._ingest_proposer_result(allocation_id, result_path):
                published.append(allocation_id)
        return published

    def _poll_experiments(self) -> list[str]:
        """Poll result files for running experiments and ingest them."""
        ingested: list[str] = []
        for attempt in self.store.running_attempts("experiment"):
            experiment_id = attempt.logical_work_id
            result_path = self.run_dir / "experiments" / experiment_id / "result.json"
            if not result_path.exists():
                continue
            if self._ingest_experiment_result(experiment_id, result_path):
                ingested.append(experiment_id)
        return ingested

    def _ingest_proposer_result(
        self,
        allocation_id: str,
        result_path: Path,
    ) -> bool:
        """Publish proposals from a completed proposer result file.

        Returns True when the result was a scientific completion (proposals
        published or an abstention); an infra failure keeps the allocation
        open for retry and returns False.
        """
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[scheduler] failed to read proposer {allocation_id}: {exc}", flush=True)
            return False

        attempt = self._latest_attempt(allocation_id, "proposer")
        if raw.get("status") == "failed":
            if attempt is not None:
                self.store.mark_proposer_infra_failed(
                    allocation_id=allocation_id,
                    attempt_id=attempt.attempt_id,
                )
            self._archive_result(
                result_path, attempt.attempt_id if attempt else None)
            return False

        result = raw.get("result", {})
        node_id = result.get("node_id")
        episode_id = result.get("episode_id")
        proposals = result.get("proposals", [])
        allocation = self.store.get_allocation(allocation_id)
        reserved = allocation.reserved_proposal_ids if allocation else ()
        if node_id and episode_id and proposals:
            self.store.publish_proposals(
                node_id=node_id,
                episode_id=episode_id,
                proposals=proposals,
                reserved_proposal_ids=reserved,
            )
        self.store.deallocate_proposer(
            allocation_id=allocation_id,
            proposals_produced=len(proposals),
        )
        if attempt is not None:
            self.store.mark_attempt_succeeded(attempt.attempt_id)
        self._archive_result(
            result_path, attempt.attempt_id if attempt else None)
        return True

    def _ingest_experiment_result(
        self,
        experiment_id: str,
        result_path: Path,
    ) -> bool:
        """Ingest an experiment result, splitting infra from scientific."""
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[scheduler] failed to read experiment {experiment_id}: {exc}", flush=True)
            return False

        attempt = self._latest_attempt(experiment_id, "experiment")
        if raw.get("status") == "failed":
            # Infrastructure failure: reopen the experiment, keep its
            # scientific status untouched (§16/§17).
            if attempt is not None:
                self.store.mark_experiment_infra_failed(
                    experiment_id=experiment_id,
                    attempt_id=attempt.attempt_id,
                )
            self._archive_result(
                result_path, attempt.attempt_id if attempt else None)
            return True

        result = raw.get("result", {})
        outcome = result.get("outcome", "COMPLETED")
        status = _EXPERIMENT_SCI_STATUS.get(outcome)
        if status is None:
            print(
                f"[scheduler] unknown experiment outcome {outcome!r} for "
                f"{experiment_id}; treating as infra",
                flush=True,
            )
            if attempt is not None:
                self.store.mark_experiment_infra_failed(
                    experiment_id=experiment_id,
                    attempt_id=attempt.attempt_id,
                )
            self._archive_result(
                result_path, attempt.attempt_id if attempt else None)
            return True

        gate_raw = result.get("gate", {})
        gate = GateDecision(
            results={
                name: GateResult(g.get("passed"), g.get("detail", ""))
                for name, g in gate_raw.get("results", {}).items()
            },
            passed=gate_raw.get("passed", False),
        )
        self.store.ingest_experiment_result(
            experiment_id=experiment_id,
            result_sha=result.get("sha"),
            metrics=result.get("metrics", {}),
            gate_result=gate,
            status=status,
            changed_paths=result.get("changed_paths", []),
            frontier_config=self._frontier_config(),
        )
        if attempt is not None:
            self.store.mark_attempt_succeeded(attempt.attempt_id)
        self._archive_result(
            result_path, attempt.attempt_id if attempt else None)
        return True

    def _latest_attempt(self, logical_work_id: str, kind: str):
        attempts = self.store.attempts_for_work(logical_work_id, kind)
        return attempts[-1] if attempts else None

    def _archive_result(self, result_path: Path, attempt_id: str | None) -> None:
        """Consume a result artifact so the reconciler cannot re-read it.

        After ingest, the fixed ``result.json`` path must no longer exist;
        otherwise the reconciler keeps feeding the same (possibly
        infra-failed) result back into the loop instead of reaching the
        ``reattach_or_wait`` branch that re-submits a fresh attempt (§18).
        Rename preserves the artifact for audit while clearing the path.
        """
        if not result_path.exists():
            return
        suffix = f".{attempt_id}.ingested" if attempt_id else ".ingested"
        result_path.rename(result_path.with_name(result_path.name + suffix))

    # ------------------------------------------------------------------
    # Reconciliation / resume
    # ------------------------------------------------------------------

    def _execute_reconcile_actions(
        self,
        actions: list,
    ) -> tuple[list[str], list[str]]:
        """Apply reconcile actions: ingest completed results, re-submit dead work."""
        published: list[str] = []
        ingested: list[str] = []
        for action in actions:
            if action.kind == "ingest_result":
                if action.work_kind == "proposer":
                    result_path = (
                        self.run_dir
                        / "proposer_allocations"
                        / action.logical_work_id
                        / "result.json"
                    )
                    if self._ingest_proposer_result(action.logical_work_id, result_path):
                        published.append(action.logical_work_id)
                elif action.work_kind == "experiment":
                    result_path = (
                        self.run_dir
                        / "experiments"
                        / action.logical_work_id
                        / "result.json"
                    )
                    if self._ingest_experiment_result(action.logical_work_id, result_path):
                        ingested.append(action.logical_work_id)
            elif action.kind == "reattach_or_wait":
                self._resubmit_if_dead(action)
        return published, ingested

    def _resubmit_if_dead(self, action) -> None:
        """Re-submit open logical work that has no running attempt.

        After ``mark_running_attempts_lost`` on startup, an open allocation or
        experiment with no running attempt is dead work from a previous process
        and gets a fresh attempt (§18).  Work that still has a running attempt
        is left alone — that is the ``wait`` branch.
        """
        if action.work_kind == "proposer":
            self._resubmit_proposer(action.logical_work_id)
        elif action.work_kind == "experiment":
            self._resubmit_experiment(action.logical_work_id)

    def _resubmit_proposer(self, allocation_id: str) -> None:
        allocation = self.store.get_allocation(allocation_id)
        if allocation is None:
            return
        if any(
            a.status == "running"
            for a in self.store.attempts_for_work(allocation_id, "proposer")
        ):
            return
        node = self._queries.get_node(allocation.node_id)
        episode = self._queries.get_episode(allocation.episode_id)
        if node is None or episode is None:
            return
        attempt = self.store.record_attempt(
            logical_work_id=allocation_id,
            kind="proposer",
            status="running",
            started_at=self.clock(),
        )
        ordinal = len(self.store.attempts_for_work(allocation_id, "proposer"))
        self.submit_proposer(
            allocation_id,
            self._proposer_payload(
                allocation, node, episode, attempt.attempt_id, ordinal),
        )

    def _resubmit_experiment(self, experiment_id: str) -> None:
        experiment = self._queries.get_experiment(experiment_id)
        if experiment is None or experiment.status not in {"pending", "running"}:
            return
        if any(
            a.status == "running"
            for a in self.store.attempts_for_work(experiment_id, "experiment")
        ):
            return
        node = self._queries.get_node(experiment.parent_node_id)
        if node is None:
            return
        attempt = self.store.record_attempt(
            logical_work_id=experiment_id,
            kind="experiment",
            status="running",
            started_at=self.clock(),
        )
        ordinal = len(self.store.attempts_for_work(experiment_id, "experiment"))
        self.store.mark_experiment_running(experiment_id)
        self.submit_experiment(
            experiment_id,
            {
                "experiment_id": experiment_id,
                "proposal_id": experiment.proposal_id,
                "parent_node_id": experiment.parent_node_id,
                "parent_sha": node.sha,
                "proposal": self._proposal_instruction(experiment.proposal_id),
                "attempt_id": attempt.attempt_id,
                "attempt": ordinal,
            },
        )

    def _proposal_instruction(self, proposal_id: str) -> str:
        proposal = self._queries.get_proposal(proposal_id)
        return proposal.instruction if proposal else ""

    # ------------------------------------------------------------------
    # Quiescence
    # ------------------------------------------------------------------

    def _quiescent(self) -> bool:
        """True when there is nothing left to do."""
        if self.store.running_attempts("proposer"):
            return False
        if self.store.running_attempts("experiment"):
            return False
        if self.store.queued_proposals():
            return False
        # Open allocations / experiments still awaiting a retry or an offline
        # result mean work remains (the reconciler will re-submit them).
        if self.store.open_allocations():
            return False
        if self.store.open_experiments():
            return False
        window = max(1, self.config.quiescence_window_proposals)
        if self._step_count - self._last_proposal_step < window:
            return False
        return True
