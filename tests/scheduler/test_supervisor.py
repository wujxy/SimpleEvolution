"""Tree-growth Supervisor gate: wake, decide, commit — never fall back."""
from __future__ import annotations

import json
from pathlib import Path

from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.scheduler.frontier import Frontier
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _gate(passed: bool = True) -> GateDecision:
    return GateDecision({"PASS": GateResult(passed, "")}, passed)


def _seed(store: ResearchStore, *, extra_child: bool = False):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=_gate(), depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id)
        child = None
        if extra_child:
            child = tx.create_node(
                parent_node_id=root.node_id, experiment_id="exp-child",
                sha="child", metrics={"score": 5}, gate_result=_gate(),
                depth=1, status="dormant",
            )
            tx.create_episode(
                inherited_from_episode_id=None, node_id=child.node_id)
    return root, episode, child


class Submitter:
    """Records supervisor/proposer submissions; results are hand-written."""

    presumes_dead_on_startup = False

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.supervisor: list[tuple[str, dict]] = []
        self.proposer: list[tuple[str, dict]] = []

    def submit_supervisor(self, work_id: str, payload: dict) -> str:
        self.supervisor.append((work_id, payload))
        return str(self.run_dir / "supervisor_decisions" / work_id / "result.json")

    def submit_proposer(self, allocation_id: str, payload: dict) -> str:
        self.proposer.append((allocation_id, payload))
        return str(self.run_dir / "proposer_allocations" / allocation_id / "result.json")

    def submit_experiment(self, experiment_id: str, payload: dict) -> str:
        return str(self.run_dir / "experiments" / experiment_id / "result.json")

    def submit_integrator(self, request_id: str, payload: dict) -> str:
        return str(self.run_dir / "integration_requests" / request_id / "result.json")

    def write_decision(self, work_id: str, result: dict, *, status="completed"):
        path = self.run_dir / "supervisor_decisions" / work_id / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "supervisor", "request_id": work_id,
            "status": status, "result": result, "error": None,
            "execution": {},
        }, ensure_ascii=False), encoding="utf-8")


def _scheduler(store, run_dir, submitter, **config):
    return Scheduler(
        store, run_dir,
        SchedulerConfig(quiescence_window_proposals=1, **config),
        submitter=submitter,
    )


def test_gate_wakes_on_events_and_creates_linked_leases(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    assert len(submitter.supervisor) == 1
    work_id, payload = submitter.supervisor[0]
    assert work_id == "supervisor-1"
    batch = payload["batch"]["event_batch"]
    assert batch["cursor_from"] == 0 and batch["cursor_to"] == 1
    assert [e["type"] for e in batch["events"]] == ["root_ready"]
    # Facts and stable ids only — no prepared ranking (invariant 6).
    assert set(batch["events"][0]) == {"event_id", "type", "payload"}
    assert submitter.proposer == []

    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth",
        "node_ids": [root.node_id], "rationale": "root first.",
        "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()

    assert store.supervisor_event_cursor() == 1
    assert store.pending_supervisor_events() == []
    (allocation,) = store.open_allocations()
    assert allocation.node_id == root.node_id
    assert allocation.decision_id == "d1"
    decision = store.get_supervisor_decision("d1")
    assert decision["node_ids"] == [root.node_id]
    assert store.latest_scheduler_event("supervisor_decision_accepted")[
        "decision_id"] == "d1"
    assert len(submitter.proposer) == 1

    # Idle capacity never re-asks without new evidence (invariant 10).
    scheduler.step()
    assert len(submitter.supervisor) == 1
    assert len(submitter.proposer) == 1


def test_gate_selects_historical_node_unrelated_to_new_event(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth", "node_ids": [],
        "rationale": "wait.", "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.supervisor_event_cursor() == 1

    # New evidence lands about the dormant child; the gate may still pick
    # the root (invariant 8).
    store.emit_supervisor_event(
        "experiment_terminal",
        {"experiment_id": "exp-child", "status": "no_change",
         "child_node_id": None, "parent_node_id": child.node_id,
         "gate_passed": False})
    scheduler.step()
    work_id2, _ = submitter.supervisor[1]
    assert work_id2 == "supervisor-2"
    submitter.write_decision(work_id2, {
        "decision_id": "d2", "decision_kind": "growth",
        "node_ids": [root.node_id], "rationale": "root is the better bet.",
        "detail": {}, "event_cursor_to": 2,
    })
    scheduler.step()
    (allocation,) = store.open_allocations()
    assert allocation.node_id == root.node_id
    assert allocation.decision_id == "d2"


def test_zero_one_and_many_selections(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_proposer_inflight=2)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-empty", "decision_kind": "growth", "node_ids": [],
        "rationale": "nothing yet.", "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.supervisor_event_cursor() == 1
    assert store.open_allocations() == []
    # An empty selection with nothing in flight quiesces (invariant 11).
    assert scheduler._quiescent() is True

    store.emit_supervisor_event(
        "experiment_terminal",
        {"experiment_id": "x", "status": "completed",
         "parent_node_id": root.node_id, "child_node_id": None,
         "gate_passed": True})
    scheduler.step()
    work_id, _ = submitter.supervisor[1]
    submitter.write_decision(work_id, {
        "decision_id": "d-two", "decision_kind": "growth",
        "node_ids": [root.node_id, child.node_id],
        "rationale": "both deserve one lease.", "detail": {},
        "event_cursor_to": 2,
    })
    scheduler.step()
    assert {a.node_id for a in store.open_allocations()} == {
        root.node_id, child.node_id}


def test_empty_selection_waits_while_work_remains(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth", "node_ids": [],
        "rationale": "wait for in-flight work.", "detail": {},
        "event_cursor_to": 1,
    })
    scheduler.step()
    assert scheduler._quiescent() is False


def test_stale_decision_is_not_applied_and_batch_is_redelivered(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    # New evidence lands while the worker is thinking.
    store.emit_supervisor_event(
        "lease_terminal",
        {"allocation_id": "a1", "node_id": root.node_id,
         "outcome": "abstain"})
    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth",
        "node_ids": [root.node_id], "rationale": "stale.",
        "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()

    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []
    assert store.get_supervisor_decision("d1") is None
    assert store.latest_scheduler_event("supervisor_decision_stale")[
        "work_id"] == work_id

    # The same session is re-woken with the larger incremental batch.
    scheduler.step()
    work_id2, payload = submitter.supervisor[1]
    assert work_id2 == "supervisor-2"
    batch = payload["batch"]["event_batch"]
    assert batch["cursor_from"] == 0 and batch["cursor_to"] == 2
    assert [e["event_id"] for e in batch["events"]] == [1, 2]


def test_worker_failures_never_fall_back_and_end_in_stall(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_proposer_inflight=1,
        poll_seconds=0.01)

    for _ in range(10):
        scheduler.step()
        if submitter.supervisor:
            work_id, _ = submitter.supervisor[-1]
            submitter.write_decision(work_id, {}, status="failed")
        if store.latest_scheduler_event("supervisor_stalled") is not None:
            break

    assert store.latest_scheduler_event("supervisor_stalled")[
        "work_id"] == "supervisor-1"
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []
    assert submitter.proposer == []
    with store._connect() as conn:
        fallbacks = conn.execute(
            "SELECT COUNT(*) FROM scheduler_events "
            "WHERE type = 'supervisor_fallback'").fetchone()[0]
    assert fallbacks == 0

    outcome = scheduler.run(max_steps=8)
    assert outcome["status"] == "stalled"
    assert submitter.proposer == []


def test_invalid_decision_is_rejected_and_retried_same_batch(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-bad", "decision_kind": "growth",
        "node_ids": ["ghost-node"], "rationale": "unknown node.",
        "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.latest_scheduler_event("supervisor_decision_rejected")[
        "work_id"] == work_id
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []

    # The retry reuses the same batch work id (same logical session).
    scheduler.step()
    assert submitter.supervisor[-1][0] == "supervisor-1"


def test_over_capacity_decision_is_rejected_whole(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_proposer_inflight=1)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-cap", "decision_kind": "growth",
        "node_ids": [root.node_id, child.node_id],
        "rationale": "too many.", "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.get_supervisor_decision("d-cap") is None
    assert store.open_allocations() == []


def test_integration_turn_creates_request_and_consumes_cursor(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, child = _seed(store, extra_child=True)
    with store.transaction() as tx:
        tx.create_proposal(Proposal(
            proposal_id="p-donor", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="donor",
            rationale={}, status="done", created_at=0,
        ))
        tx.create_experiment(
            experiment_id="exp-donor", proposal_id="p-donor",
            parent_node_id=root.node_id, status="running")
    # Give the donor a gate-passed child so it qualifies as a donor.
    store.ingest_experiment_result(
        experiment_id="exp-donor", result_sha="donor-sha",
        metrics={}, gate_result=_gate(True), status="completed")
    # Neutralize the event the ingest just emitted so the test flow is the
    # only wake source.
    head = store.supervisor_event_head()
    store.commit_supervisor_decision(
        decision_id="d-sync", work_id=f"supervisor-{head}",
        node_ids=[], rationale="sync", cursor_to=head)

    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-int", "decision_kind": "integration_request",
        "node_ids": [], "rationale": "branches matured.",
        "detail": {
            "integration_request_id": "req-1",
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["exp-donor"],
            "selection_rationale": "complementary validated results",
        },
        "event_cursor_to": store.supervisor_event_head(),
    })
    scheduler.step()

    request = store.get_integration_request("req-1")
    assert request is not None and request.status == "open"
    assert store.supervisor_event_cursor() == store.supervisor_event_head()
    assert store.get_supervisor_decision("d-int")["decision_kind"] == (
        "integration_request")


def test_baseline_frontier_mode_still_allocates_without_supervisor(
    tmp_path: Path,
):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)

    submitted: list[tuple[str, dict]] = []
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(quiescence_window_proposals=1),
        submit_proposer=lambda allocation_id, payload: submitted.append(
            (allocation_id, payload)) or "",
        submit_experiment=lambda experiment_id, payload: "",
    )
    scheduler._allocate_proposers(Frontier({root.node_id}, {}))

    assert len(submitted) == 1
    # Baseline leases are not Supervisor decisions (invariant 1 applies to
    # supervisor runs only).
    (allocation,) = store.open_allocations()
    assert allocation.decision_id is None
