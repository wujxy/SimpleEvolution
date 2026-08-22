"""Scheduler event loop: allocate proposers, drain queue, ingest results."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import FrontierAxis, GateDecision, GateResult, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator, load_generator_basis, select_one_generator
from simpleevo.jobs.base import BaseSubmitter
from simpleevo.research_state import research_state_to_dict
from proposer.supervisor import (
    build_group_snapshot,
    decision_from_dict,
    validate_decision,
    validate_integration_request,
)

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

_SUPERVISOR_WAITING = object()


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
        submitter: BaseSubmitter | None = None,
        submit_proposer: Callable[[str, dict[str, Any]], str] | None = None,
        submit_experiment: Callable[[str, dict[str, Any]], str] | None = None,
        supervisor_decider: Callable[[Any, int], Any] | None = None,
        clock: Callable[[], float] = time.time,
        generator_basis: list[Generator] | None = None,
        stop_allocating: bool = False,
    ):
        self.store = store
        self.run_dir = Path(run_dir)
        self.config = config
        self.evolution_config = evolution_config
        # A submitter object (Local or Condor) is the preferred wiring: it
        # provides both submit callables AND the live-probe hooks the
        # Reconciler needs.  Bare callables are kept for backward compatibility
        # (tests) — no probe, backend-unaware reconciliation.
        self._submitter = submitter
        if submitter is not None:
            self.submit_proposer = submitter.submit_proposer
            self.submit_experiment = submitter.submit_experiment
            self.submit_supervisor = getattr(submitter, "submit_supervisor", None)
            self.submit_integrator = getattr(submitter, "submit_integrator", None)
        else:
            self.submit_proposer = submit_proposer or (lambda _aid, _p: "")
            self.submit_experiment = submit_experiment or (lambda _eid, _p: "")
            self.submit_supervisor = None
            self.submit_integrator = None
        self.clock = clock
        self.supervisor_decider = supervisor_decider
        # When True, ``step()`` stops allocating new proposers; in-flight work
        # (running proposers/experiments and queued proposals) is still drained
        # until the scheduler reaches quiescence.  Used by the ablation driver
        # to turn the eval/budget cap into an actual stop for tree runs, which
        # otherwise never quiesce while frontier nodes keep research budget.
        self.stop_allocating = stop_allocating
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
        # Condor jobs DO survive the scheduler, so the submitter keeps running
        # attempts and the Reconciler reconciles each against the live queue.
        if self._submitter is None or self._submitter.presumes_dead_on_startup:
            self.store.mark_running_attempts_lost()

    def step(self) -> dict[str, Any]:
        """Run one scheduler iteration.  Returns telemetry for the step."""
        self._step_count += 1

        # 1. Reconcile offline results + re-submit dead work.  The submitter
        # hook lets a condor backend tell the reconciler which missing-result
        # jobs are still alive vs HELD/gone (which get retried).
        reconciler = Reconciler(self.store, self.run_dir, submitter=self._submitter)
        reconcile_actions = reconciler.reconcile()
        self._execute_reconcile_actions(reconcile_actions)

        # 1b. Evidence change (tree-growth design §4): the seed world is
        # presented to the growth gate exactly once.
        if self.store.supervisor_event_head() == 0:
            root = self._queries.root_node()
            if root is not None:
                self.store.emit_supervisor_event(
                    "root_ready", {"root_node_id": root.node_id})

        # 2. Compute frontier.
        frontier = self._compute_frontier()

        # 3. Allocate proposers to frontier nodes.  Suppressed once the driver
        #    has hit its eval/budget cap — the run then only drains in-flight
        #    work (running proposers publish; queued proposals become
        #    experiments; running experiments complete) and quiesces.
        proposer_jobs = (
            [] if self.stop_allocating else self._allocate_proposers(frontier)
        )

        # A Supervisor-created integration request runs as an independent,
        # temporary main-writer job rather than consuming a Scientist lease.
        integrator_jobs = self._schedule_integrators()

        # 4. Poll proposer results and publish proposals.
        published = self._poll_proposers()
        integrated = self._poll_integrators()
        if published:
            self._last_proposal_step = self._step_count

        # 5. Drain executor queue up to capacity.  Suppressed once the driver
        #    has hit its cap: queued proposals are abandoned (left queued in
        #    L2, never turned into new experiments), so the run only drains
        #    experiments already in flight.
        experiment_jobs = (
            [] if self.stop_allocating else self._drain_executor_queue()
        )

        # 6. Poll running experiments for completed results.
        ingested = self._poll_experiments()
        self._resolve_integration_outcomes()

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
            "integrator_jobs": len(integrator_jobs),
            "integrated": len(integrated),
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
        """Create proposer leases from Supervisor, or Frontier on failure."""
        capacity = self._proposer_capacity()
        if capacity <= 0:
            return []

        directives = None
        if self.supervisor_decider is not None:
            try:
                snapshot = build_group_snapshot(
                    self.store,
                    max_research_per_node=self._max_research_per_node(),
                    max_proposals_per_node=self._max_proposals_per_node(),
                )
                decision = validate_decision(
                    snapshot,
                    self.supervisor_decider(snapshot, capacity),
                    proposer_capacity=capacity,
                )
                self._accept_integration_request(
                    snapshot, decision.integration_request,
                )
                self._apply_epoch_review(decision.epoch_review)
                directives = [
                    (item.node_id, item.proposal_slots)
                    for item in decision.allocations
                ]
                self.store.record_scheduler_event(
                    "supervisor_decision_accepted",
                    {
                        "decision_id": decision.decision_id,
                        "snapshot_watermark": snapshot.watermark,
                        "allocations": [
                            {"node_id": node_id, "proposal_slots": slots}
                            for node_id, slots in directives
                        ],
                    },
                )
            except Exception as exc:
                self.store.record_scheduler_event(
                    "supervisor_fallback",
                    {"error": str(exc)},
                )
                directives = None
        elif self.submit_supervisor is not None:
            outcome = self._supervisor_job_directives(capacity)
            if outcome is _SUPERVISOR_WAITING:
                return []
            directives = outcome

        if directives is None:
            if not frontier.node_ids:
                return []
            directives = [
                (node_id, self.config.proposal_slots)
                for node_id in sample_proposer_nodes(
                    frontier,
                    self._allocations_counter,
                    capacity,
                    self._frontier_config(),
                )
            ]

        jobs = []
        for node_id, proposal_slots in directives:
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
                proposal_slots=proposal_slots,
                max_proposals_per_node=self._max_proposals_per_node(),
            )
            if allocation is None:
                continue
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

    def _supervisor_job_directives(self, capacity: int):
        """Submit or ingest one stateless Supervisor worker.

        While the worker is live no Frontier allocation competes with it.
        Frontier becomes eligible only when that worker fails or its artifact
        is invalid, preserving the designed primary/fallback relationship.
        """
        snapshot = build_group_snapshot(
            self.store,
            max_research_per_node=self._max_research_per_node(),
            max_proposals_per_node=self._max_proposals_per_node(),
        )
        previous = self.store.latest_scheduler_event(
            "supervisor_decision_accepted",
        )
        if (
            previous is not None
            and previous.get("snapshot_watermark") == snapshot.watermark
            and not previous.get("allocations")
        ):
            return []
        running = self.store.running_attempts("supervisor")
        if running:
            attempt = running[-1]
            result_path = (
                self.run_dir / "supervisor_decisions"
                / attempt.logical_work_id / "result.json"
            )
            if not result_path.exists():
                return _SUPERVISOR_WAITING
            try:
                raw = json.loads(result_path.read_text(encoding="utf-8"))
                if raw.get("status") != "completed":
                    raise ValueError(raw.get("error") or "supervisor worker failed")
                decision = validate_decision(
                    snapshot,
                    decision_from_dict(raw.get("result", {})),
                    proposer_capacity=capacity,
                )
                self._accept_integration_request(
                    snapshot, decision.integration_request,
                )
                self._apply_epoch_review(decision.epoch_review)
            except Exception as exc:
                self.store.mark_attempt_failed(attempt.attempt_id)
                self.store.record_scheduler_event(
                    "supervisor_fallback",
                    {"decision_id": attempt.logical_work_id, "error": str(exc)},
                )
                self._archive_result(result_path, attempt.attempt_id)
                return None
            self.store.mark_attempt_succeeded(attempt.attempt_id)
            directives = [
                (item.node_id, item.proposal_slots)
                for item in decision.allocations
            ]
            self.store.record_scheduler_event(
                "supervisor_decision_accepted",
                {
                    "decision_id": decision.decision_id,
                    "snapshot_watermark": snapshot.watermark,
                    "allocations": [
                        {"node_id": node_id, "proposal_slots": slots}
                        for node_id, slots in directives
                    ],
                },
            )
            self._archive_result(result_path, attempt.attempt_id)
            return directives

        work_id = f"supervisor-{snapshot.watermark[:24]}"
        attempt = self.store.record_attempt(
            logical_work_id=work_id,
            kind="supervisor",
            status="running",
            started_at=self.clock(),
        )
        self.submit_supervisor(work_id, {
            "snapshot": asdict(snapshot),
            "proposer_capacity": capacity,
            "attempt_id": attempt.attempt_id,
        })
        return _SUPERVISOR_WAITING

    def _accept_integration_request(self, snapshot, raw) -> None:
        if raw is None:
            return
        normalized = validate_integration_request(
            self.store, snapshot.epoch_id, raw,
        )
        open_requests = self.store.integration_requests("open")
        if open_requests and all(
            item.integration_request_id != normalized["integration_request_id"]
            for item in open_requests
        ):
            raise ValueError("another integration request is already open")
        request = self.store.create_integration_request(**normalized)
        self.store.record_scheduler_event(
            "integration_request_created",
            {
                "integration_request_id": request.integration_request_id,
                "epoch_id": request.epoch_id,
                "target_node_id": request.target_node_id,
                "donor_experiment_ids": list(request.donor_experiment_ids),
            },
        )

    def _resolve_integration_outcomes(self) -> None:
        """Close scientifically rejected integrations without semantic review."""
        for request in self.store.integration_requests("submitted"):
            if not request.experiment_id:
                continue
            experiment = self._queries.get_experiment(request.experiment_id)
            if experiment is None:
                continue
            if experiment.status in {"gate_rejected", "no_change"}:
                self.store.finish_integration_request(
                    request.integration_request_id, status="closed",
                )
                self.store.record_scheduler_event(
                    "integration_candidate_rejected",
                    {
                        "integration_request_id": request.integration_request_id,
                        "experiment_id": experiment.experiment_id,
                        "status": experiment.status,
                    },
                )

    def _apply_epoch_review(self, review) -> None:
        if review is None:
            return
        request_id = str(review.get("integration_request_id", "")).strip()
        action = str(review.get("action", "")).strip()
        rationale = str(review.get("rationale", "")).strip()
        if not request_id or action not in {"promote", "retain"} or not rationale:
            raise ValueError("invalid epoch review")
        request = self.store.get_integration_request(request_id)
        experiment = (
            self._queries.get_experiment(request.experiment_id)
            if request is not None and request.experiment_id else None
        )
        if (
            request is None or request.status != "submitted"
            or experiment is None or experiment.status != "completed"
            or not experiment.gate_result.passed or not experiment.child_node_id
        ):
            raise ValueError("epoch review requires a gate-passed candidate")
        if action == "promote":
            epoch = self.store.promote_integration_epoch(request_id)
            payload = {
                "integration_request_id": request_id,
                "epoch_id": epoch.epoch_id,
                "root_node_id": epoch.root_node_id,
                "rationale": rationale,
                "evidence_refs": list(review.get("evidence_refs", ())),
            }
            self.store.record_scheduler_event("epoch_promoted", payload)
        else:
            self.store.finish_integration_request(request_id, status="closed")
            self.store.record_scheduler_event(
                "integration_candidate_retained",
                {
                    "integration_request_id": request_id,
                    "rationale": rationale,
                    "evidence_refs": list(review.get("evidence_refs", ())),
                },
            )

    # ------------------------------------------------------------------
    # Integration requests
    # ------------------------------------------------------------------

    def _schedule_integrators(self) -> list[str]:
        if self.submit_integrator is None:
            return []
        running_ids = {
            attempt.logical_work_id
            for attempt in self.store.running_attempts("integrator")
        }
        jobs = []
        for request in self.store.integration_requests("open"):
            if request.integration_request_id in running_ids:
                continue
            request = self.store.prepare_integration_request(
                request.integration_request_id,
            )
            proposal_id = f"integration-{request.integration_request_id}"
            attempt = self.store.record_attempt(
                logical_work_id=request.integration_request_id,
                kind="integrator", status="running", started_at=self.clock(),
            )
            self.submit_integrator(request.integration_request_id, {
                "request": asdict(request),
                "episode_id": request.integrator_episode_id,
                "proposal_id": proposal_id,
                "public_evidence": self._integration_evidence(request),
                "attempt_id": attempt.attempt_id,
            })
            jobs.append(request.integration_request_id)
        return jobs

    def _integration_evidence(self, request) -> dict[str, Any]:
        target = self._queries.get_node(request.target_node_id)
        donors = []
        for experiment_id in request.donor_experiment_ids:
            experiment = self._queries.get_experiment(experiment_id)
            if experiment is None:
                continue
            proposal = self._queries.get_proposal(experiment.proposal_id)
            parent = self._queries.get_node(experiment.parent_node_id)
            child = (
                self._queries.get_node(experiment.child_node_id)
                if experiment.child_node_id else None
            )
            donors.append({
                "experiment": asdict(experiment),
                "proposal": None if proposal is None else asdict(proposal),
                "parent": None if parent is None else asdict(parent),
                "child": None if child is None else asdict(child),
            })
        return {
            "target": None if target is None else asdict(target),
            "donors": donors,
            "group_experiments": [
                asdict(item) for item in self._queries.list_experiments()
            ],
        }

    def _poll_integrators(self) -> list[str]:
        completed = []
        for attempt in self.store.running_attempts("integrator"):
            request_id = attempt.logical_work_id
            result_path = (
                self.run_dir / "integration_requests" / request_id / "result.json"
            )
            if not result_path.exists():
                continue
            try:
                raw = json.loads(result_path.read_text(encoding="utf-8"))
                if raw.get("status") != "completed":
                    raise RuntimeError(raw.get("error") or "integrator worker failed")
                result = raw.get("result", {})
                request = self.store.get_integration_request(request_id)
                if request is None or request.status != "open":
                    raise ValueError("integration request is not open")
                if result.get("outcome") == "abstained":
                    self.store.finish_integration_request(
                        request_id, status="abstained",
                    )
                elif result.get("outcome") == "submitted":
                    proposal = result.get("proposal") or {}
                    state = result.get("research_state") or {}
                    donors = tuple(proposal.get("donor_experiment_ids", ()))
                    if not donors or not set(donors).issubset(
                        set(request.donor_experiment_ids)
                    ):
                        raise ValueError("Integrator used donors outside its request")
                    self.store.publish_research_batch(
                        node_id=request.target_node_id,
                        episode_id=request.integrator_episode_id,
                        transformations=(), research_states=(state,),
                        proposals=(proposal,),
                        reserved_proposal_ids=(proposal["proposal_id"],),
                    )
                    self.store.finish_integration_request(
                        request_id, status="submitted",
                        proposal_id=proposal["proposal_id"],
                    )
                else:
                    raise ValueError("unknown Integrator outcome")
            except Exception as exc:
                print(f"[scheduler] invalid Integrator result {request_id}: {exc}", flush=True)
                self.store.mark_attempt_failed(attempt.attempt_id)
                self._archive_result(result_path, attempt.attempt_id)
                continue
            self.store.mark_attempt_succeeded(attempt.attempt_id)
            self._archive_result(result_path, attempt.attempt_id)
            completed.append(request_id)
        return completed

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
        carries one suggested variation operator (``variation_operator``)
        not yet tried on this node, so each re-study offers the optional mentor
        a fresh cognitive axis. Bounded by
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
        """Select one untried generator as a reseed suggestion.

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
                tried.add(episode.variation_operator)
        chosen = select_one_generator(basis, tried)
        return chosen.id if chosen else None

    def _generator_basis_or_load(self) -> list[Generator]:
        if self._generator_basis is None:
            self._generator_basis = load_generator_basis()
        return self._generator_basis

    def _max_research_per_node(self) -> int:
        if self.evolution_config is not None:
            return self.evolution_config.max_research_per_node
        return 3

    def _max_proposals_per_node(self) -> int:
        if self.evolution_config is not None:
            return self.evolution_config.max_proposals_per_node
        return 9

    def _proposer_payload(
        self, allocation, node, episode, attempt_id: str, attempt: int,
    ) -> dict[str, Any]:
        """Assemble the proposer job payload from L2 (also used on re-submit)."""
        seed = self._research_state_seed_for(node)
        payload = {
            "allocation_id": allocation.allocation_id,
            "node_id": node.node_id,
            "node_sha": node.sha,
            "episode_id": episode.episode_id,
            "inherited_from_episode_id": episode.inherited_from_episode_id,
            "generator_basis": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                }
                for item in self._generator_basis_or_load()
            ],
            "suggested_operator_id": episode.variation_operator,
            "proposal_ids": list(allocation.reserved_proposal_ids),
            "attempt_id": attempt_id,
            "attempt": attempt,
        }
        if seed:
            payload["research_state_seed"] = seed
        else:
            payload["world_transition"] = self._world_transition_for(node)
        return payload

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

    def _research_state_seed_for(self, node) -> dict[str, Any]:
        """Join the one State/Proposal/Experiment path that produced a Child."""
        if node.experiment_id is None:
            return {}
        experiment = self._queries.get_experiment(node.experiment_id)
        if experiment is None:
            return {}
        proposal = self._queries.get_proposal(experiment.proposal_id)
        if proposal is None or not proposal.research_state_id:
            return {}
        state = self._queries.get_research_state(proposal.research_state_id)
        if state is None:
            return {}
        facts = self._world_transition_for(node)
        return {
            "child_node": {
                "node_id": node.node_id,
                "sha": node.sha,
                "metrics": dict(node.metrics),
                "gate": {
                    "passed": node.gate_result.passed,
                    "results": {
                        name: {"passed": result.passed, "detail": result.detail}
                        for name, result in node.gate_result.results.items()
                    },
                },
            },
            "originating_research_state": research_state_to_dict(state),
            "proposal": {
                "proposal_id": proposal.proposal_id,
                "instruction": proposal.instruction,
                "expectation": proposal.rationale.get("expectation"),
                "material_difference": proposal.rationale.get(
                    "material_difference"
                ),
            },
            "experiment": {
                "experiment_id": experiment.experiment_id,
                "parent_node_id": experiment.parent_node_id,
                "metrics": facts.get("metrics", {}),
                "gate": facts.get("gate", {}),
                "changed_paths": facts.get("diff", []),
                "parent_metrics": facts.get("parent_metrics", {}),
            },
        }

    # ------------------------------------------------------------------
    # Executor queue
    # ------------------------------------------------------------------

    def _experiment_capacity(self) -> int:
        return self.config.max_experiment_inflight - self.store.count_running_attempts("experiment")

    def _drain_executor_queue(self):
        """Submit admitted queued proposals as experiment jobs up to capacity."""
        queue = ExecutorQueue(
            self.store,
            self.config.queue or QueueConfig(),
        )
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
            self.store.link_integration_experiment(
                proposal_id, experiment.experiment_id,
            )
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
        transformations = result.get("transformations", [])
        research_states = result.get("research_states", [])
        proposals = result.get("proposals", [])
        allocation = self.store.get_allocation(allocation_id)
        reserved = allocation.reserved_proposal_ids if allocation else ()
        try:
            if allocation is None:
                raise ValueError(f"unknown proposer allocation: {allocation_id}")
            if node_id != allocation.node_id or episode_id != allocation.episode_id:
                raise ValueError("proposer result belongs to another node or episode")
            self.store.publish_research_batch(
                node_id=node_id,
                episode_id=episode_id,
                transformations=transformations,
                research_states=research_states,
                proposals=proposals,
                reserved_proposal_ids=reserved,
            )
        except Exception as exc:
            print(
                f"[scheduler] invalid proposer result {allocation_id}: {exc}",
                flush=True,
            )
            if attempt is not None:
                self.store.mark_proposer_infra_failed(
                    allocation_id=allocation_id,
                    attempt_id=attempt.attempt_id,
                )
            self._archive_result(
                result_path, attempt.attempt_id if attempt else None,
            )
            return False
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
                # Supervisor artifacts are validated against the freshly built
                # snapshot by _allocate_proposers later in this same step.
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
        elif action.work_kind == "supervisor":
            self._resubmit_supervisor(action.logical_work_id)
        elif action.work_kind == "integrator":
            self._resubmit_integrator(action.logical_work_id)

    def _resubmit_supervisor(self, work_id: str) -> None:
        if self.submit_supervisor is None or self.store.running_attempts("supervisor"):
            return
        manifest_path = (
            self.run_dir / "supervisor_decisions" / work_id / "manifest.json"
        )
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))["payload"]
        except (OSError, KeyError, json.JSONDecodeError):
            return
        attempt = self.store.record_attempt(
            logical_work_id=work_id,
            kind="supervisor",
            status="running",
            started_at=self.clock(),
        )
        payload["attempt_id"] = attempt.attempt_id
        self.submit_supervisor(work_id, payload)

    def _resubmit_integrator(self, work_id: str) -> None:
        if self.submit_integrator is None or any(
            item.logical_work_id == work_id
            for item in self.store.running_attempts("integrator")
        ):
            return
        manifest_path = (
            self.run_dir / "integration_requests" / work_id / "manifest.json"
        )
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))["payload"]
        except (OSError, KeyError, json.JSONDecodeError):
            return
        attempt = self.store.record_attempt(
            logical_work_id=work_id, kind="integrator", status="running",
            started_at=self.clock(),
        )
        payload["attempt_id"] = attempt.attempt_id
        self.submit_integrator(work_id, payload)

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

    def _in_flight(self) -> bool:
        """True while any proposer or experiment worker is running.

        The cap-bypass counterpart to ``_quiescent``: once the driver has
        declared the run capped (``stop_allocating``), it only needs in-flight
        work to drain — it deliberately ignores queued proposals and open
        allocations, which are abandoned rather than blocking termination.
        """
        return bool(
            self.store.running_attempts("proposer")
            or self.store.running_attempts("experiment")
            or self.store.running_attempts("supervisor")
            or self.store.running_attempts("integrator")
        )

    def _quiescent(self) -> bool:
        """True when there is nothing left to do."""
        if self.store.running_attempts("proposer"):
            return False
        if self.store.running_attempts("experiment"):
            return False
        if self.store.running_attempts("supervisor"):
            return False
        if self.store.running_attempts("integrator"):
            return False
        if self.store.integration_requests("open"):
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
