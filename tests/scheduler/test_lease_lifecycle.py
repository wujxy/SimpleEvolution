"""Scheduler-side lease lifecycle tests: three exits, write-back, capacity.

科学家完整研究制 §2.3/2.4 — the lease state machine as driven by the
Scheduler (not the store internals, which tests/db/test_lease_store.py
covers): delivery → adjudication → write-back reopen / conclude, the
vacuous-exit correction round-trip, budget force-conclude, and the
capacity/quiescence interactions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simpleevo.contracts import GateDecision, GateResult
from simpleevo.db.lease_writer import upsert_lease_research_state
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


class _Submitter:
    presumes_dead_on_startup = False

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.scientist: list[tuple[str, dict]] = []
        self.experiments: list[tuple[str, dict]] = []

    def submit_supervisor(self, work_id, payload):
        return str(self.run_dir / "supervisor_decisions" / work_id / "result.json")

    def submit_proposer(self, allocation_id, payload):
        self.scientist.append((allocation_id, payload))
        return str(self.run_dir / "proposer_allocations" / allocation_id / "result.json")

    def submit_experiment(self, experiment_id, payload):
        self.experiments.append((experiment_id, payload))
        return str(self.run_dir / "experiments" / experiment_id / "result.json")

    def submit_integrator(self, request_id, payload):
        return str(self.run_dir / "integration_requests" / request_id / "result.json")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def env(tmp_path):
    run_dir = tmp_path
    store = ResearchStore(run_dir / "simpleevo.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-root",
            metrics={"lps": 100.0}, gate_result=GateDecision({}, True),
            depth=0, status="active",
        )
        episode = tx.create_episode(node_id=root.node_id)
    allocation = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id,
    )
    upsert_lease_research_state(
        store.path, lease_id=allocation.allocation_id,
        episode_id=episode.episode_id, node_id=root.node_id,
        working_model="memo on file",
    )
    submitter = _Submitter(run_dir)
    scheduler = Scheduler(
        store, run_dir,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=1),
        submitter=submitter,
    )
    return run_dir, store, root, episode, allocation, submitter, scheduler


def _deliver(run_dir, store, root, episode, allocation, sha, submitter,
             scheduler, handover="short handover"):
    store.record_attempt(
        logical_work_id=allocation.allocation_id, kind="proposer",
        status="running", started_at=1.0,
    )
    _write_json(
        run_dir / "proposer_allocations" / allocation.allocation_id
        / "result.json",
        {"status": "completed", "result": {"conclusion": {
            "kind": "deliver", "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "world_sha": sha, "handover": handover,
        }}},
    )
    scheduler.step()
    experiment_id, payload = submitter.experiments[-1]
    assert payload["eval_only"] is True
    return experiment_id


def _adjudicate(run_dir, experiment_id, scheduler, *, passed, sha):
    _write_json(
        run_dir / "experiments" / experiment_id / "result.json",
        {"status": "completed", "result": {
            "outcome": "COMPLETED" if passed else "GATE_REJECTED",
            "sha": sha, "metrics": {"lps": 120.0},
            "gate": {"passed": passed, "results": {
                "VERIFY": {"passed": passed, "detail": ""}}},
            "changed_paths": [],
        }},
    )
    assert scheduler._poll_experiments() == [experiment_id]


def test_gate_reject_reopens_the_seat_with_wake_feedback(env):
    (run_dir, store, root, episode, allocation, submitter,
     scheduler) = env
    experiment_id = _deliver(
        run_dir, store, root, episode, allocation, "a" * 40, submitter,
        scheduler)
    _adjudicate(run_dir, experiment_id, scheduler, passed=False, sha="a" * 40)
    # The reopen's resubmit is launched at the end of the next step.
    scheduler.step()

    # The lease reopened and the seat was resubmitted — same allocation,
    # same episode, a fresh attempt.
    allocation_row = store.get_allocation(allocation.allocation_id)
    assert allocation_row.state == "reopen" or (
        allocation_row.state == "researching")  # after _submit_pending_reopens
    assert allocation_row.finished_at is None
    assert allocation_row.reopen_count == 1
    assert len(submitter.scientist) == 1  # the reopen's resubmit
    attempts = store.attempts_for_work(allocation.allocation_id, "proposer")
    assert len(attempts) == 2

    # The reopened seat sees the adjudication feedback at wake.
    from scientist.wake import build_wake_view

    feedback = ResearchQueries(store.path).lease_adjudication_for_episode(
        episode.episode_id)
    assert feedback is not None
    assert feedback["gate"]["VERIFY"]["passed"] is False


def test_reopen_budget_exhaustion_concludes_rejected(env):
    (run_dir, store, root, episode, allocation, submitter,
     scheduler) = env
    scheduler.config = SchedulerConfig(
        max_proposer_inflight=1, max_experiment_inflight=1,
        max_lease_reopens=1,
    )
    shas = ["a" * 40, "b" * 40]
    for sha in shas:
        experiment_id = _deliver(
            run_dir, store, root, episode, allocation, sha, submitter,
            scheduler)
        _adjudicate(
            run_dir, experiment_id, scheduler, passed=False, sha=sha)

    concluded = store.get_allocation(allocation.allocation_id)
    assert concluded.finished_at is not None
    assert concluded.state == "concluded_rejected"
    # one reopen then rejection on the second delivery
    assert concluded.reopen_count == 1
    assert len(submitter.experiments) == 2


def test_lease_wall_budget_forces_cut_off_at_write_back(env):
    (run_dir, store, root, episode, allocation, submitter,
     scheduler) = env
    scheduler.config = SchedulerConfig(
        max_proposer_inflight=1, max_experiment_inflight=1,
        lease_wall_budget_seconds=0.0,
    )
    experiment_id = _deliver(
        run_dir, store, root, episode, allocation, "a" * 40, submitter,
        scheduler)
    _adjudicate(run_dir, experiment_id, scheduler, passed=False, sha="a" * 40)

    concluded = store.get_allocation(allocation.allocation_id)
    assert concluded.state == "concluded_cut_off"
    assert concluded.finished_at is not None


def test_vacuous_exit_gets_one_correction_round_trip(tmp_path):
    run_dir = tmp_path
    store = ResearchStore(run_dir / "t.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-r",
            metrics={}, gate_result=GateDecision({}, True), depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=root.node_id)
    allocation = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id,
    )
    # NO research state registered — vacuous exit.
    store.record_attempt(
        logical_work_id=allocation.allocation_id, kind="proposer",
        status="running", started_at=1.0,
    )
    result_path = (
        run_dir / "proposer_allocations" / allocation.allocation_id
        / "result.json"
    )
    _write_json(result_path, {"status": "completed", "result": {"conclusion": {
        "kind": "abstain", "node_id": root.node_id,
        "episode_id": episode.episode_id, "axes_checked": ["x"],
    }}})
    submitter = _Submitter(run_dir)
    scheduler = Scheduler(
        store, run_dir,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=1),
        submitter=submitter,
    )
    scheduler.step()
    # First vacuous exit: correction round-trip (attempt failed, lease open).
    attempts = store.attempts_for_work(allocation.allocation_id, "proposer")
    assert attempts[-1].status == "failed"
    assert store.get_allocation(allocation.allocation_id).finished_at is None
    assert ResearchQueries(store.path).lease_conclusion_rejection_count(
        allocation.allocation_id) == 1

    # Second vacuous exit: accepted as the honest final word (cut_off).
    store.record_attempt(
        logical_work_id=allocation.allocation_id, kind="proposer",
        status="running", started_at=2.0,
    )
    _write_json(result_path, {"status": "completed", "result": {"conclusion": {
        "kind": "abstain", "node_id": root.node_id,
        "episode_id": episode.episode_id, "axes_checked": ["x"],
    }}})
    scheduler.step()
    concluded = store.get_allocation(allocation.allocation_id)
    assert concluded.finished_at is not None
    assert concluded.state == "concluded_cut_off"


def test_awaiting_lease_frees_seat_and_parks_quiescence(env):
    (run_dir, store, root, episode, allocation, submitter,
     scheduler) = env
    experiment_id = _deliver(
        run_dir, store, root, episode, allocation, "a" * 40, submitter,
        scheduler)
    queries = ResearchQueries(store.path)
    # The adjudication experiment is live work: not quiescent.
    assert scheduler._quiescent() is False
    # But the parked lease frees the proposer seat.
    assert queries.researching_open_allocation_count() == 0
    # And the reconciler never resubmits the parked scientist (this lease
    # was allocated directly, so zero scientist launches — and it stays
    # zero while the experiment runs).
    for _ in range(2):
        scheduler.step()
    assert len(store.attempts_for_work(
        allocation.allocation_id, "proposer")) == 1
    assert len(submitter.scientist) == 0

    _adjudicate(run_dir, experiment_id, scheduler, passed=True, sha="a" * 40)
    concluded = store.get_allocation(allocation.allocation_id)
    assert concluded.state == "concluded_delivered"
    with store.transaction() as tx:
        child = tx._conn.execute(
            "SELECT sha FROM nodes WHERE parent_node_id = ?",
            (root.node_id,),
        ).fetchone()
    assert child["sha"] == "a" * 40


def test_restart_between_delivery_ingest_and_resubmit_is_idempotent(env):
    (run_dir, store, root, episode, allocation, submitter,
     scheduler) = env
    experiment_id = _deliver(
        run_dir, store, root, episode, allocation, "a" * 40, submitter,
        scheduler)
    # Rebuild the scheduler (restart) with the lease parked mid-adjudication
    # and the experiment attempt running: exactly one experiment attempt,
    # the seat is never relaunched.
    scheduler2 = Scheduler(
        store, run_dir,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=1),
        submitter=submitter,
    )
    scheduler2.step()
    assert len(store.attempts_for_work(experiment_id, "experiment")) == 1
    assert len(submitter.scientist) == 0  # the parked seat is never relaunched
    with store.transaction() as tx:
        n = tx._conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE episode_id = ?",
            (episode.episode_id,),
        ).fetchone()
    assert n["n"] == 1  # no double mint
