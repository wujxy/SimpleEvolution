"""Scheduler event loop: allocate proposers, drain queue, ingest results."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from simpleevo.config import EvolutionConfig
from simpleevo.db.store import (
    FrontierAxis,
    GateDecision,
    GateResult,
    LeaseSpec,
    ResearchStore,
    StaleSupervisorDecision,
)
from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator, load_generator_basis
from simpleevo.jobs.base import BaseSubmitter
from .admission import validate_integration_request

from .frontier import (
    FrontierConfig,
    build_policy,
    compute_frontier,
    sample_proposer_nodes,
)
from .queue import ExecutorQueue, QueueConfig
from .reconcile import Reconciler
from .telemetry import TelemetryRecorder, spend_usd


@dataclass(frozen=True)
class SchedulerConfig:
    max_proposer_inflight: int = 2
    max_experiment_inflight: int = 2
    # Frontier-baseline mode only (ablation loop arm).  Seat purchases pin
    # this to 1 in _seat_leases; do not grow it into the seat contract.
    proposal_slots: int = 3
    queue: QueueConfig | None = None
    frontier: FrontierConfig | None = None
    poll_seconds: float = 5.0
    quiescence_window_proposals: int = 2
    # Driver budget policy, durable in the run_limits table once installed:
    # the growth gate reads it to weigh remaining budget, and changing an
    # installed limit emits a durable ``budget_changed`` event.
    max_terminal_evals: int | None = None
    budget_usd: float | None = None


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
        submitter: BaseSubmitter | None = None,
        submit_proposer: Callable[[str, dict[str, Any]], str] | None = None,
        submit_experiment: Callable[[str, dict[str, Any]], str] | None = None,
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
        # When True, ``step()`` stops allocating new proposers; in-flight work
        # (running proposers/experiments and queued proposals) is still drained
        # until the scheduler reaches quiescence.  Used by the ablation driver
        # to turn the eval/budget cap into an actual stop for tree runs, which
        # otherwise never quiesce while frontier nodes keep research budget.
        self.stop_allocating = stop_allocating
        # Durable cap state (eval/budget limit already reached), recomputed
        # from the database + usage ledger at the top of every step, before
        # any new work starts.  Together with ``stop_allocating`` this is
        # the allocation-disabled condition, so a restarted capped run, a
        # plain ``run()``, and a bounded driver all behave identically.
        self._durable_cap_reached = False
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
        # Harvest durable results first.  Reattach/resubmit is deliberately
        # deferred until after the durable cap is recomputed: a result ingested
        # here may itself consume the last eval/budget, while a run that was
        # already capped must not restart a backend-reported lost worker.
        ingest_actions = [
            action for action in reconcile_actions
            if action.kind == "ingest_result"
        ]
        retry_actions = [
            action for action in reconcile_actions
            if action.kind != "ingest_result"
        ]
        self._execute_reconcile_actions(ingest_actions)

        # 1b. Evidence change (tree-growth design §4): the seed world is
        # presented to the growth gate exactly once.
        if self.store.supervisor_event_head() == 0:
            root = self._queries.root_node()
            if root is not None:
                self.store.emit_supervisor_event(
                    "root_ready", {
                        "root_node_id": root.node_id,
                        # The baseline is first-hand: every later judgment
                        # weighs nodes against it (design §6 facts-only).
                        "root_metrics": dict(root.metrics),
                    })

        # 1c. Budget policy is durable and rebuildable: install the
        # configured limits each step and wake the gate only when an
        # installed limit actually changed (a restart with the same
        # configuration stays silent).
        self._sync_run_limits()
        # 1d. Derive the durable cap from the same eval/spend data the
        # budget view reads — before any new work starts this step.
        self._durable_cap_reached = self._compute_durable_run_limit()

        # Only now may dead work be considered for reattachment.  Supervisor
        # and Integrator retries observe the freshly derived cap and therefore
        # cannot start after the run has become allocation-disabled.
        self._execute_reconcile_actions(retry_actions)

        # 2. Compute frontier.
        frontier = self._compute_frontier()

        # 3. Allocation.  Once allocation is disabled — the driver declared
        #    the cap (``stop_allocating``) or a durable run limit is already
        #    reached — the scheduler initiates no new logical work, but a
        #    Supervisor result already on disk is still harvested (closing
        #    the attempt, unapplied), so a capped run drains instead of
        #    wedging on a running gate worker.
        proposer_jobs = self._allocate_proposers(frontier)

        # A Supervisor-created integration request runs as an independent,
        # temporary main-writer job rather than consuming a Scientist lease.
        # A capped run never *starts* one; jobs already in flight are still
        # harvested by _poll_integrators.
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
            (
                [] if self._allocation_disabled()
                else self._drain_executor_queue()
            )
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
            "supervisor_pending": (
                len(self.store.pending_supervisor_events())
                if self.submit_supervisor is not None else 0
            ),
            "published": len(published),
            "integrator_jobs": len(integrator_jobs),
            "integrated": len(integrated),
            "experiment_jobs": len(experiment_jobs),
            "ingested": len(ingested),
            "reconcile_actions": len(reconcile_actions),
        }

    def run(self, *, max_steps: int | None = None) -> dict[str, Any]:
        """Run the scheduler until quiescence, stall, cap drain, or max_steps.

        ``stalled`` means the growth gate exhausted its bounded retries with
        an unconsumed batch: the run parks instead of silently quiescing or
        falling back to Frontier.  ``capped`` means allocation is
        disabled (driver stop or a durable run limit reached) and in-flight
        work has drained — evidence left unconsumed does not keep a capped
        run spinning.
        """
        telemetry: list[dict[str, Any]] = []
        status = "quiesced"
        while True:
            if max_steps is not None and self._step_count >= max_steps:
                status = "max_steps"
                break
            step_telemetry = self.step()
            telemetry.append(step_telemetry)
            if self._supervisor_stalled() and not self._in_flight():
                status = "stalled"
                break
            if self._allocation_disabled() and not self._in_flight():
                status = "capped"
                break
            if self._quiescent():
                break
            time.sleep(self.config.poll_seconds)
        return {
            "steps": self._step_count, "status": status, "telemetry": telemetry,
        }

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
        """Create proposer leases from the Supervisor growth gate, or from
        the explicit Frontier baseline when no supervisor is configured.

        With allocation disabled the baseline mode allocates nothing; the
        Supervisor gate still runs because it owns result harvesting (a
        completed worker must close its attempt even on a capped run).
        """
        if self.submit_supervisor is not None:
            return self._run_supervisor_gate()
        if self._allocation_disabled():
            return []
        return self._allocate_frontier_baseline(frontier)

    def _allocate_frontier_baseline(self, frontier):
        """Non-Supervisor baseline mode (ablation / GEPA runs)."""
        capacity = self._proposer_capacity()
        if capacity <= 0:
            return []
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
        return self._create_leases(directives)

    def _create_leases(self, directives) -> list[str]:
        """Turn (node_id, proposal_slots) directives into submitted leases."""
        jobs = []
        for node_id, proposal_slots in directives:
            episode = self._idle_episode_for_node(node_id)
            if episode is None:
                # Node whose episodes are all terminal: re-study it by
                # reseeding a fresh episode (GEPA pool semantics).
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
            )
            if allocation is None:
                continue
            self._launch_lease(allocation, node, episode)
            jobs.append(allocation.allocation_id)
        return jobs

    def _launch_lease(self, allocation, node, episode) -> None:
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

    # ------------------------------------------------------------------
    # Supervisor growth gate (tree-growth design §7/§9)
    # ------------------------------------------------------------------

    def _run_supervisor_gate(self) -> list[str]:
        """Wake the growth gate on pending evidence; apply one decision.

        Never falls back to Frontier: a failed or invalid worker result
        keeps the batch unconsumed and retries the same logical session,
        bounded by ``supervisor_max_retries``; exhaustion records
        ``supervisor_stalled`` and parks allocation visibly.

        Once allocation is disabled (driver stop or durable cap), the gate
        only harvests: a result already on disk closes its attempt and is
        archived unapplied (``supervisor_decision_discarded``), and no new
        gate worker is submitted — a judgment formed under a budget state
        that no longer holds must not derive new work.
        """
        running = self.store.running_attempts("supervisor")
        if running:
            attempt = running[-1]
            result_path = (
                self.run_dir / "supervisor_decisions"
                / attempt.logical_work_id / "result.json"
            )
            if not result_path.exists():
                return []
            if self._allocation_disabled():
                return self._discard_supervisor_result(attempt, result_path)
            return self._ingest_supervisor_result(attempt, result_path)

        if self._allocation_disabled():
            return []
        pending = self.store.pending_supervisor_events()
        if not pending:
            return []
        head = pending[-1].event_id
        work_id = f"supervisor-{head}"
        if self._supervisor_exhausted(work_id):
            self._record_supervisor_stall(work_id)
            return []
        attempt = self.store.record_attempt(
            logical_work_id=work_id,
            kind="supervisor",
            status="running",
            started_at=self.clock(),
        )
        self.submit_supervisor(
            work_id, self._supervisor_payload(pending, attempt))
        return []

    def _ingest_supervisor_result(self, attempt, result_path) -> list[str]:
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._reject_supervisor_result(
                attempt, result_path, f"unreadable result: {exc}")
            return []
        if raw.get("status") != "completed":
            self._reject_supervisor_result(
                attempt, result_path,
                str(raw.get("error") or "supervisor worker failed"))
            return []
        result = raw.get("result", {})
        try:
            jobs = self._apply_supervisor_decision(attempt, result)
        except StaleSupervisorDecision as exc:
            # New evidence arrived mid-turn.  The decision is not partially
            # applied; the batch stays unconsumed and the same session is
            # re-woken with the larger incremental batch.
            self.store.mark_attempt_succeeded(attempt.attempt_id)
            self.store.record_scheduler_event(
                "supervisor_decision_stale",
                {
                    "work_id": attempt.logical_work_id,
                    "decision_id": result.get("decision_id"),
                    "event_head": exc.head,
                },
            )
            self._archive_result(result_path, attempt.attempt_id)
            return []
        except Exception as exc:
            self._reject_supervisor_result(attempt, result_path, str(exc))
            return []
        self.store.mark_attempt_succeeded(attempt.attempt_id)
        self._archive_result(result_path, attempt.attempt_id)
        return jobs

    def _discard_supervisor_result(self, attempt, result_path) -> list[str]:
        """Close a completed gate worker without applying its judgment.

        Used when the run hit its cap mid-turn: the decision was made under
        a budget state that no longer holds, so it must not create leases,
        requests, or reviews.  The attempt succeeded (the worker did its
        job), the artifact is archived, and the batch stays unconsumed — a
        resumed run re-wakes on it.  Not a failure, not a retry.
        """
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            decision_id = (raw.get("result") or {}).get("decision_id")
        except Exception:
            decision_id = None
        self.store.mark_attempt_succeeded(attempt.attempt_id)
        self.store.record_scheduler_event(
            "supervisor_decision_discarded",
            {
                "work_id": attempt.logical_work_id,
                "decision_id": decision_id,
                "reason": "run capped (allocation disabled)",
            },
        )
        self._archive_result(result_path, attempt.attempt_id)
        return []

    def _reject_supervisor_result(self, attempt, result_path, reason) -> None:
        self.store.mark_attempt_failed(attempt.attempt_id)
        self.store.record_scheduler_event(
            "supervisor_decision_rejected",
            {"work_id": attempt.logical_work_id, "error": reason},
        )
        self._archive_result(result_path, attempt.attempt_id)

    def _apply_supervisor_decision(self, attempt, result) -> list[str]:
        decision_id = str(result.get("decision_id") or "")
        if not decision_id:
            raise ValueError("supervisor result missing decision_id")
        cursor_to = int(result.get("event_cursor_to", -1))
        decision_kind = str(result.get("decision_kind") or "growth")
        rationale = str(result.get("rationale", ""))
        node_ids = [str(item) for item in result.get("node_ids", [])]
        detail = dict(result.get("detail") or {})

        if decision_kind == "growth":
            purchases = self._seat_purchases(result)
            if not purchases and self._untried_seats_remain() and not (
                    self._work_in_flight()):
                # Honest quiescence (seat design §2.4): an empty selection
                # is a wait while evidence is in flight, and a completion
                # only when no purchasable seat remains.  With untried
                # seats on the table and nothing in flight, empty would
                # stillbirth the program with questions unasked — reject
                # it back with the fact that makes it wrong.
                raise ValueError(
                    "empty growth decision while untried seats remain and "
                    "no work is in flight: waiting needs in-flight "
                    "evidence; completing requires the untried set to be "
                    "empty. Buy a seat, or name what you are waiting for "
                    "by leaving work running."
                )
            leases = self._seat_leases(purchases)
            commit = self.store.commit_supervisor_decision(
                decision_id=decision_id,
                work_id=attempt.logical_work_id,
                decision_kind="growth",
                node_ids=[node_id for node_id, _lens in purchases],
                rationale=rationale,
                detail={
                    "seat_purchases": [
                        {"node_id": node_id, "lens": lens}
                        for node_id, lens in purchases
                    ],
                },
                cursor_to=cursor_to,
                leases=leases,
            )
            return self._launch_decision_leases(commit.allocations)
        if decision_kind == "integration_request":
            epoch = self.store.current_epoch()
            if epoch is None:
                raise ValueError("cannot accept integration without an epoch")
            # The request id is mechanical harness state, never model
            # output: keyed on the work id it stays stable across retries
            # of the same batch (idempotent redelivery) and changes when a
            # stale batch grows — exactly when the judgment itself changes.
            detail["integration_request_id"] = (
                f"ir-{attempt.logical_work_id}")
            normalized = validate_integration_request(
                self.store, epoch.epoch_id, detail)
            # The request row is created inside the decision transaction:
            # a stale decision leaves zero side effects (design §9).
            self.store.commit_supervisor_decision(
                decision_id=decision_id,
                work_id=attempt.logical_work_id,
                decision_kind="integration_request",
                node_ids=[],
                rationale=rationale,
                detail=normalized,
                cursor_to=cursor_to,
                integration_request=normalized,
            )
            return []
        if decision_kind == "epoch_review":
            # Promotion/retention happens inside the decision transaction;
            # the store re-validates the candidate on the same snapshot.
            self.store.commit_supervisor_decision(
                decision_id=decision_id,
                work_id=attempt.logical_work_id,
                decision_kind="epoch_review",
                node_ids=[],
                rationale=rationale,
                detail=dict(detail),
                cursor_to=cursor_to,
                epoch_review={
                    "integration_request_id": detail.get(
                        "integration_request_id"),
                    "action": detail.get("review"),
                    "rationale": rationale,
                    "evidence_refs": list(detail.get("evidence_refs", ())),
                },
            )
            return []
        raise ValueError(f"unknown decision kind: {decision_kind}")

    @staticmethod
    def _seat_purchases(result) -> list[tuple[str, str]]:
        """Parse a growth decision's ``seat_purchases`` into (node, lens)."""
        raw = result.get("seat_purchases")
        if raw is None:
            raise ValueError(
                "growth decision must carry seat_purchases "
                '[{"node_id": ..., "lens": ...}] (the node_ids field is '
                "gone)"
            )
        if not isinstance(raw, list):
            raise ValueError("seat_purchases must be a list")
        purchases: list[tuple[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("each seat purchase must be an object")
            node_id = item.get("node_id")
            lens = item.get("lens")
            extra = set(item) - {"node_id", "lens"}
            if extra:
                raise ValueError(
                    f"seat purchase may carry only node_id and lens; "
                    f"unexpected: {sorted(extra)}"
                )
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("seat purchase node_id must be non-empty")
            if not isinstance(lens, str) or not lens:
                raise ValueError("seat purchase lens must be non-empty")
            purchases.append((node_id, lens))
        return purchases

    def _seat_leases(self, purchases) -> list[LeaseSpec]:
        """Mechanical legality for every purchased seat (seat design §7.2).

        Whole-decision validation: any violation rejects the decision, it is
        never partially applied.  The harness enforces the legality contract
        only — capacity, lens validity, lineage dedup, one episode per seat —
        and never chooses or ranks lenses.
        """
        capacity = self._proposer_capacity()
        if len(purchases) > capacity:
            raise ValueError(
                "supervisor decision exceeds proposer capacity: "
                f"{len(purchases)} purchases > {capacity} free seats"
            )
        basis = self._generator_basis_or_load()
        if not basis:
            raise ValueError(
                "lens basis is empty; no seat can be validated"
            )
        known = {item.id for item in basis}
        burned = self._burned_lenses()
        parents = {
            node.node_id: node.parent_node_id
            for node in self._queries.list_nodes()
        }

        def _related(a: str, b: str) -> bool:
            """True when a is an ancestor of b (or vice versa, checked by
            the caller with swapped args)."""
            current = parents.get(b)
            hops = 0
            while current and hops < 100:
                if current == a:
                    return True
                current = parents.get(current)
                hops += 1
            return False

        seen: set[tuple[str, str]] = set()
        claimed: set[str] = set()
        leases: list[LeaseSpec] = []
        for node_id, lens in purchases:
            if (node_id, lens) in seen:
                raise ValueError(
                    f"duplicate seat purchase: {node_id} x {lens}"
                )
            seen.add((node_id, lens))
            if lens not in known:
                raise ValueError(f"unknown lens: {lens}")
            node = self._queries.get_node(node_id)
            if node is None or node.status == "dead":
                raise ValueError(
                    f"seat purchase on a non-allocatable node: {node_id}"
                )
            lineage_burned = burned.get(node_id, frozenset())
            if lens in lineage_burned:
                raise ValueError(
                    f"lens {lens} already burned on {node_id} or its "
                    "ancestry; buy an untried lens (see the untried fact)"
                )
            # Intra-decision lineage dedup: the transaction is atomic, so
            # the post-decision state must satisfy lineage dedup regardless
            # of purchase order — a lens this decision buys on an ancestor
            # (or on a descendant) of node_id burns it here too.  The
            # precomputed snapshot above cannot see sibling purchases.
            for prior_node, prior_lens in seen:
                if (prior_lens == lens and prior_node != node_id
                        and (_related(prior_node, node_id)
                             or _related(node_id, prior_node))):
                    raise ValueError(
                        f"lens {lens} already burned on {node_id} or its "
                        "ancestry; buy an untried lens (see the untried fact)"
                    )
            episode = self._seat_episode_for_node(node_id, claimed)
            if episode is None:
                raise ValueError(
                    f"no episode available for seat purchase: {node_id}"
                )
            claimed.add(episode.episode_id)
            leases.append(LeaseSpec(
                node_id=node_id,
                episode_id=episode.episode_id,
                proposal_slots=1,
                lens=lens,
            ))
        return leases

    def _burned_lenses(self) -> dict[str, set[str]]:
        """Shared tree function (queries.burned_lenses): enforcement here,
        facts in supervisor_facts — one implementation, two consumers."""
        return self._queries.burned_lenses()

    def _seat_episode_for_node(self, node_id: str, claimed: set[str]):
        """A distinct fresh episode for one seat on ``node_id``.

        The node's never-allocated episode serves its first seat; every
        further concurrent or later seat gets its own NEW episode (seats are
        independent research acts — a seat never resumes a sibling seat's
        session).  ``claimed`` holds episode ids taken by earlier purchases
        of the SAME decision, which have no allocation row yet.
        """
        allocated = self.store.allocated_episode_ids() | claimed
        for episode in self._queries.episodes_for_node(node_id, limit=1000):
            if episode.episode_id not in allocated:
                return episode
        return self._new_seat_episode(node_id)

    def _new_seat_episode(self, node_id: str):
        """Create a fresh episode for a further seat on ``node_id``.

        Deliberately inherits nothing (``inherited_from_episode_id`` None):
        same-node seats are siblings, not continuations — each wakes on the
        node's own facts (Child-world pack) and its lens.  No research
        budget applies (the dissolved ``max_research_per_node``); the seat
        cost is priced by the Supervisor's purchase against real budget.
        """
        with self.store.transaction() as tx:
            return tx.create_episode(
                node_id=node_id,
                inherited_from_episode_id=None,
                variation_operator=None,
            )

    def _launch_decision_leases(self, allocations) -> list[str]:
        jobs = []
        for allocation in allocations:
            node = self._queries.get_node(allocation.node_id)
            episode = self._queries.get_episode(allocation.episode_id)
            if node is None or episode is None:
                continue
            self._launch_lease(allocation, node, episode)
            jobs.append(allocation.allocation_id)
        return jobs

    def _sync_run_limits(self) -> None:
        """Install the configured budget limits.

        ``install_run_limits`` writes any change and its ``budget_changed``
        wake event in one transaction; there is deliberately no second
        emit here — a crash between the two writes could never leave the
        intervention silently swallowed.
        """
        limits = {
            "max_terminal_evals": self.config.max_terminal_evals,
            "budget_usd": self.config.budget_usd,
        }
        if all(value is None for value in limits.values()):
            return
        self.store.install_run_limits(limits)

    def _allocation_disabled(self) -> bool:
        """No new logical work may start.

        Either the driver declared the cap (``stop_allocating``) or a
        durable run limit is already reached — the two causes are kept
        distinct so a driver's manual stop is never silently overwritten.
        """
        return self.stop_allocating or self._durable_cap_reached

    def _compute_durable_run_limit(self) -> bool:
        """True when an installed eval/budget limit is already reached.

        Reads the same numbers the budget view shows: terminal experiments
        against ``max_terminal_evals``, and the shared usage-ledger spend
        against ``budget_usd`` (only priceable when token pricing is
        configured).  No limits installed -> never capped.
        """
        limits = self.store.run_limits()
        if not limits:
            return False
        max_evals = limits.get("max_terminal_evals")
        if max_evals is not None and self._queries.terminal_experiment_count() >= int(
            max_evals
        ):
            return True
        budget_usd = limits.get("budget_usd")
        if budget_usd is not None:
            pricing = (
                dict(self.evolution_config.pricing)
                if self.evolution_config is not None
                and self.evolution_config.pricing else {}
            )
            if pricing and spend_usd(self.run_dir, pricing) >= float(
                budget_usd
            ):
                return True
        return False

    def _supervisor_payload(self, pending, attempt) -> dict[str, Any]:
        """IDs and static knobs only — the worker rebuilds the batch and
        runtime facts from the store at wake time (module contract §3:
        a queued payload must not age)."""
        head = pending[-1].event_id
        cfg = self.evolution_config
        supervisor_steps = (
            int(getattr(cfg, "supervisor_steps", 40)) if cfg is not None
            else 40
        )
        return {
            "work_id": attempt.logical_work_id,
            "attempt_id": attempt.attempt_id,
            "decision_id": uuid.uuid4().hex,
            "supervisor_steps": supervisor_steps,
            "event_batch_bounds": {
                "cursor_from": self.store.supervisor_event_cursor(),
                "cursor_to": head,
            },
            "knobs": {
                "max_proposer_inflight": self.config.max_proposer_inflight,
                "max_experiment_inflight": self.config.max_experiment_inflight,
                "max_terminal_evals": self.config.max_terminal_evals,
                "budget_usd": self.config.budget_usd,
                "pricing": dict(cfg.pricing) if (
                    cfg is not None and cfg.pricing) else None,
                "metrics_schema": (
                    dict(cfg.metrics_schema)
                    if cfg is not None else None),
            },
            "run_context": {"goal": cfg.goal} if cfg is not None else {},
        }

    # ------------------------------------------------------------------
    # Seat/lens facts (seat design §7.4)
    # ------------------------------------------------------------------

    def _supervisor_max_retries(self) -> int:
        if self.evolution_config is not None:
            value = getattr(self.evolution_config, "supervisor_max_retries", 3)
            return int(value or 3)
        return 3

    def _supervisor_exhausted(self, work_id: str) -> bool:
        attempts = self.store.attempts_for_work(work_id, "supervisor")
        failures = [
            item for item in attempts
            if item.status in {"failed", "lost"}
        ]
        return len(failures) >= self._supervisor_max_retries()

    def _record_supervisor_stall(self, work_id: str) -> None:
        latest = self.store.latest_scheduler_event("supervisor_stalled")
        if latest is not None and latest.get("work_id") == work_id:
            return
        self.store.record_scheduler_event(
            "supervisor_stalled",
            {
                "work_id": work_id,
                "attempts": len(
                    self.store.attempts_for_work(work_id, "supervisor")),
            },
        )

    def _supervisor_stalled(self) -> bool:
        """True when the gate is parked on the current pending head."""
        if self.submit_supervisor is None:
            return False
        latest = self.store.latest_scheduler_event("supervisor_stalled")
        if latest is None:
            return False
        pending = self.store.pending_supervisor_events()
        if not pending:
            return False
        return latest.get("work_id") == f"supervisor-{pending[-1].event_id}"

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

    # ------------------------------------------------------------------
    # Integration requests
    # ------------------------------------------------------------------

    def _schedule_integrators(self) -> list[str]:
        if self.submit_integrator is None:
            return []
        # A capped run never starts integrator work (the request stays open
        # for a resumed run); results of jobs already in flight are still
        # harvested by _poll_integrators.
        if self._allocation_disabled():
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
                        research_states=(state,),
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
        """Fresh episode for a FRONTIER-BASELINE (non-Supervisor) re-study.

        Inherits the node's most recent episode's final cognition (GEPA pool
        semantics — this path studies, not seats).  Supervisor-mode seats use
        ``_seat_episode_for_node`` instead, which never inherits a sibling
        seat's session.
        """
        episodes = self._queries.episodes_for_node(node_id, limit=1000)
        most_recent = episodes[0] if episodes else None  # last_active_at DESC
        if most_recent is None:
            return None
        if not self.store.episode_allocation_finished(most_recent.episode_id):
            # Previous study is still in flight: no frozen final cognition
            # to inherit yet, so this node cannot be re-studied right now.
            return None
        with self.store.transaction() as tx:
            return tx.create_episode(
                node_id=node_id,
                inherited_from_episode_id=most_recent.episode_id,
                variation_operator=None,
            )

    def _generator_basis_or_load(self) -> list[Generator]:
        if self._generator_basis is None:
            self._generator_basis = load_generator_basis()
        return self._generator_basis

    def _work_in_flight(self) -> bool:
        """True while any research work is running or awaiting evaluation.

        The honest-quiescence check: an empty growth decision may WAIT on
        this, but may not STOP past it.
        """
        return bool(
            self.store.running_attempts("proposer")
            or self.store.running_attempts("experiment")
            or self.store.running_attempts("integrator")
            or self.store.queued_proposals()
            or self.store.open_allocations()
            or self.store.open_experiments()
            or self.store.integration_requests("open")
        )

    def _untried_seats_remain(self) -> bool:
        """True when some node still has an untried lens to buy."""
        if not self._generator_basis_or_load():
            return False
        burned = self._burned_lenses()
        for node in self._queries.list_nodes():
            if node.status == "dead":
                continue
            if len(burned.get(node.node_id, ())) < len(
                    self._generator_basis_or_load()):
                return True
        return False

    def _proposer_payload(
        self, allocation, node, episode, attempt_id: str, attempt: int,
    ) -> dict[str, Any]:
        """IDs only (also used on re-submit): the worker assembles its own
        worldview from the store at wake time (module contract §3)."""
        return {
            "allocation_id": allocation.allocation_id,
            "node_id": node.node_id,
            "episode_id": episode.episode_id,
            "proposal_ids": list(allocation.reserved_proposal_ids),
            "proposal_slots": len(allocation.reserved_proposal_ids),
            "attempt_id": attempt_id,
            "attempt": attempt,
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
        research_states = result.get("research_states", [])
        proposals = result.get("proposals", [])
        allocation = self.store.get_allocation(allocation_id)
        reserved = allocation.reserved_proposal_ids if allocation else ()
        try:
            if allocation is None:
                raise ValueError(f"unknown proposer allocation: {allocation_id}")
            if node_id != allocation.node_id or episode_id != allocation.episode_id:
                raise ValueError("proposer result belongs to another node or episode")
            if not proposals and not research_states:
                # Empty-seat exit contract (seat design §7.6): an abstain
                # must leave its memo behind — at least one registered
                # research_state — or the whole investigation evaporates
                # (v5 study-3: 37 steps, zero states, hallucinated stop).
                # Give the seat exactly one correction round-trip to file
                # its memo; a second empty exit is accepted as the seat's
                # honest final word rather than looping forever.
                attempts = self.store.attempts_for_work(
                    allocation_id, "proposer")
                if len(attempts) <= 1:
                    raise ValueError(
                        "empty-seat exit without a registered research "
                        "state: an abstaining seat must first register its "
                        "memo (what it checked along its lens axes and why "
                        "they are empty) so the investigation does not "
                        "evaporate"
                    )
            self.store.publish_research_batch(
                node_id=node_id,
                episode_id=episode_id,
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
        # A capped run never re-runs the gate: its result could only be
        # discarded unapplied.
        if self._allocation_disabled():
            return
        if self._supervisor_exhausted(work_id):
            self._record_supervisor_stall(work_id)
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
        # A capped run never (re-)starts integrator work.
        if self._allocation_disabled():
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
        if self.submit_supervisor is not None:
            # Evidence changes must be judged before the run may rest.  A
            # stalled gate parks the run visibly (run() reports "stalled"),
            # so it does not keep quiescence blocked here.
            if (self.store.pending_supervisor_events()
                    and not self._supervisor_stalled()):
                return False
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
