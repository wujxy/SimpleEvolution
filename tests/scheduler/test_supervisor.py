"""Tree-growth Supervisor gate: wake, decide, commit — never fall back."""
from __future__ import annotations

import json
from pathlib import Path

from simpleevo.config import EvolutionConfig
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
        self.integrator: list[tuple[str, dict]] = []

    def submit_supervisor(self, work_id: str, payload: dict) -> str:
        self.supervisor.append((work_id, payload))
        return str(self.run_dir / "supervisor_decisions" / work_id / "result.json")

    def submit_proposer(self, allocation_id: str, payload: dict) -> str:
        self.proposer.append((allocation_id, payload))
        return str(self.run_dir / "proposer_allocations" / allocation_id / "result.json")

    def submit_experiment(self, experiment_id: str, payload: dict) -> str:
        return str(self.run_dir / "experiments" / experiment_id / "result.json")

    def submit_integrator(self, request_id: str, payload: dict) -> str:
        self.integrator.append((request_id, payload))
        return str(self.run_dir / "integration_requests" / request_id / "result.json")

    def write_decision(self, work_id: str, result: dict, *, status="completed"):
        path = self.run_dir / "supervisor_decisions" / work_id / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "supervisor", "request_id": work_id,
            "status": status, "result": result, "error": None,
            "execution": {},
        }, ensure_ascii=False), encoding="utf-8")

    def write_integrator_result(
            self, request_id: str, result: dict, *, status="completed"):
        path = (
            self.run_dir / "integration_requests" / request_id / "result.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "integrator", "request_id": request_id,
            "status": status, "result": result, "error": None,
            "execution": {},
        }, ensure_ascii=False), encoding="utf-8")


def _scheduler(store, run_dir, submitter, **config):
    return Scheduler(
        store, run_dir,
        SchedulerConfig(quiescence_window_proposals=1, **config),
        submitter=submitter,
    )


def test_gate_batch_carries_first_hand_facts(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_terminal_evals=5)

    scheduler.step()
    (_, payload) = submitter.supervisor[0]
    batch = payload["batch"]

    # Baseline metrics ride with root_ready.
    assert batch["event_batch"]["events"][0]["payload"]["root_metrics"] == {
        "score": 10}

    # The candidate set carries measured metrics, in creation order —
    # never ordered by any quality signal (invariant 6).
    nodes = batch["allocatable_nodes"]
    assert [n["node_id"] for n in nodes] == [root.node_id, child.node_id]
    assert nodes[0]["metrics"] == {"score": 10}
    assert nodes[1]["metrics"] == {"score": 5}

    # No prior rejection on this batch: nothing to correct.
    assert "previous_rejection" not in batch

    # Budget facts: limits are useless without the spent amounts.
    facts = payload["runtime_facts"]
    assert facts["terminal_evals_used"] == 0
    assert facts["remaining_terminal_evals"] == 5
    # Capacity facts: the free count, not just the ceiling — the gate
    # must see the wall before hitting it (v3: 8 blind capacity hits).
    assert facts["free_proposer_capacity"] == scheduler._proposer_capacity()


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
        "seat_purchases": [{"node_id": root.node_id, "lens": "G5"}],
        "rationale": "root first through the inversion lens.",
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
    assert decision["detail"]["seat_purchases"] == [
        {"node_id": root.node_id, "lens": "G5"}]
    # Seat mechanics: exactly one reserved proposal id (oneness is a
    # harness fact), and the lens is stamped on the episode atomically.
    assert len(allocation.reserved_proposal_ids) == 1
    episode = scheduler._queries.get_episode(allocation.episode_id)
    assert episode.variation_operator == "G5"
    # The proposer payload carries the seat identity block, not a catalog.
    (allocation_id, payload) = submitter.proposer[0]
    assert payload["seat"]["lens_id"] == "G5"
    assert payload["seat"]["directive"]
    assert payload["seat"]["forbidden"]
    assert payload["seat"]["self_check"]
    assert "suggested_operator_id" not in payload
    assert "generator_basis" not in payload
    assert payload["proposal_slots"] == 1
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
        "decision_id": "d1", "decision_kind": "growth",
        "seat_purchases": [{"node_id": root.node_id, "lens": "G4"}],
        "rationale": "root, symmetry lens first.", "detail": {},
        "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.supervisor_event_cursor() == 1

    # New evidence lands about the dormant child; the gate may still pick
    # the historical root over the newest event's node (invariant 8) —
    # through a lens its lineage has not burned.
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
        "seat_purchases": [{"node_id": root.node_id, "lens": "G7"}],
        "rationale": "root is the better bet.", "detail": {},
        "event_cursor_to": 2,
    })
    scheduler.step()
    # d1's seat is still open alongside d2's — the historical pick joined
    # it on the same node through a different lens.
    d2 = [a for a in store.open_allocations() if a.decision_id == "d2"]
    assert len(d2) == 1
    assert d2[0].node_id == root.node_id


def test_zero_one_and_many_selections(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_proposer_inflight=2)

    # An empty purchase list with untried seats remaining and nothing in
    # flight is REJECTED — honest quiescence (seat design §2.4): waiting
    # needs in-flight evidence, completing needs an exhausted untried set.
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-empty", "decision_kind": "growth",
        "seat_purchases": [], "rationale": "nothing yet.", "detail": {},
        "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []
    assert "untried seats remain" in store.latest_scheduler_event(
        "supervisor_decision_rejected")["error"]
    assert scheduler._quiescent() is False

    store.emit_supervisor_event(
        "experiment_terminal",
        {"experiment_id": "x", "status": "completed",
         "parent_node_id": root.node_id, "child_node_id": None,
         "gate_passed": True})
    scheduler.step()
    work_id, _ = submitter.supervisor[1]
    submitter.write_decision(work_id, {
        "decision_id": "d-two", "decision_kind": "growth",
        "seat_purchases": [
            {"node_id": root.node_id, "lens": "G5"},
            {"node_id": child.node_id, "lens": "G2"},
        ],
        "rationale": "both deserve a seat.", "detail": {},
        "event_cursor_to": 2,
    })
    scheduler.step()
    seats = {(a.node_id,
              scheduler._queries.get_episode(a.episode_id).variation_operator)
             for a in store.open_allocations()}
    assert seats == {(root.node_id, "G5"), (child.node_id, "G2")}


def test_two_seats_on_one_node_get_distinct_episodes_and_lenses(
        tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_proposer_inflight=2)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-seats", "decision_kind": "growth",
        "seat_purchases": [
            {"node_id": root.node_id, "lens": "G1"},
            {"node_id": root.node_id, "lens": "G3"},
        ],
        "rationale": "two schools on one world.", "detail": {},
        "event_cursor_to": 1,
    })
    scheduler.step()
    allocations = store.open_allocations()
    assert len(allocations) == 2
    assert len({a.episode_id for a in allocations}) == 2
    lenses = {
        scheduler._queries.get_episode(a.episode_id).variation_operator
        for a in allocations
    }
    assert lenses == {"G1", "G3"}
    # The first seat consumed the node's never-allocated episode; the
    # second got a fresh sibling episode (no session inheritance).
    episodes = scheduler._queries.episodes_for_node(
        root.node_id, limit=100)
    assert len(episodes) == 2
    assert all(e.inherited_from_episode_id is None for e in episodes)


def test_lineage_dedup_rejects_burned_lens(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth",
        "seat_purchases": [{"node_id": root.node_id, "lens": "G5"}],
        "rationale": "buy G5.", "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert len(store.open_allocations()) == 1

    # Same lens on the same node: a repeated bet — rejected whole.
    store.emit_supervisor_event("goal_changed", {"goal": "x"})
    scheduler.step()
    work_id2, _ = submitter.supervisor[1]
    submitter.write_decision(work_id2, {
        "decision_id": "d2", "decision_kind": "growth",
        "seat_purchases": [{"node_id": root.node_id, "lens": "G5"}],
        "rationale": "buy G5 again.", "detail": {}, "event_cursor_to": 2,
    })
    scheduler.step()
    assert store.get_supervisor_decision("d2") is None
    error = store.latest_scheduler_event(
        "supervisor_decision_rejected")["error"]
    assert "already burned" in error


def test_empty_selection_completes_when_untried_exhausted(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, child = _seed(store, extra_child=True)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)
    basis = scheduler._generator_basis_or_load()

    # Burn every lens on every living node: the untried set is exhausted.
    with store.transaction() as tx:
        for node in (root, child):
            for item in basis:
                tx.create_episode(
                    node_id=node.node_id,
                    inherited_from_episode_id=None,
                    variation_operator=item.id,
                )

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    untried = submitter.supervisor[0][1]["batch"]["untried"]
    assert all(not row["lenses"] for row in untried)
    submitter.write_decision(work_id, {
        "decision_id": "d-done", "decision_kind": "growth",
        "seat_purchases": [], "rationale": "no question remains unbought.",
        "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.supervisor_event_cursor() == 1
    assert store.get_supervisor_decision("d-done") is not None


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
        "decision_id": "d1", "decision_kind": "growth",
        "seat_purchases": [], "rationale": "wait for in-flight work.",
        "detail": {}, "event_cursor_to": 1,
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
        "seat_purchases": [{"node_id": root.node_id, "lens": "G5"}],
        "rationale": "stale.", "detail": {}, "event_cursor_to": 1,
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
        "seat_purchases": [{"node_id": "ghost-node", "lens": "G5"}],
        "rationale": "unknown node.", "detail": {}, "event_cursor_to": 1,
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
        "seat_purchases": [
            {"node_id": root.node_id, "lens": "G5"},
            {"node_id": child.node_id, "lens": "G2"},
        ],
        "rationale": "too many.", "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()
    assert store.get_supervisor_decision("d-cap") is None
    assert store.open_allocations() == []

    # The retry wakes the same session on the same batch — it must carry
    # the recorded reason, or the session re-decides blind (v3: capacity
    # rejections repeated to stall, blind to the cause).
    scheduler.step()
    (retry_id, retry_payload) = submitter.supervisor[1]
    assert retry_id == work_id
    assert "exceeds proposer capacity" in retry_payload["batch"][
        "previous_rejection"]
    assert "Submit a corrected decision" in retry_payload["batch"][
        "previous_rejection"]


def _seed_donor(store, root, episode):
    """A gate-passed donor experiment hanging off the root."""
    with store.transaction() as tx:
        tx.create_proposal(Proposal(
            proposal_id="p-donor", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="donor",
            rationale={}, status="done", created_at=0,
        ))
        tx.create_experiment(
            experiment_id="exp-donor", proposal_id="p-donor",
            parent_node_id=root.node_id, status="running")
    store.ingest_experiment_result(
        experiment_id="exp-donor", result_sha="donor-sha",
        metrics={}, gate_result=_gate(True), status="completed")


def _sync_cursor(store) -> int:
    """Consume pending events so the test flow is the only wake source."""
    head = store.supervisor_event_head()
    if head:
        store.commit_supervisor_decision(
            decision_id=f"d-sync-{head}", work_id=f"supervisor-{head}",
            node_ids=[], rationale="sync", cursor_to=head)
    return store.supervisor_event_cursor()


def test_integration_turn_creates_request_and_consumes_cursor(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, child = _seed(store, extra_child=True)
    _seed_donor(store, root, episode)
    # Neutralize the event the ingest just emitted so the test flow is the
    # only wake source.
    _sync_cursor(store)

    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    submitter.write_decision(work_id, {
        "decision_id": "d-int", "decision_kind": "integration_request",
        "node_ids": [], "rationale": "branches matured.",
        "detail": {
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["exp-donor"],
            "selection_rationale": "complementary validated results",
        },
        "event_cursor_to": store.supervisor_event_head(),
    })
    scheduler.step()

    # The harness assigned the request id from the work: stable across
    # retries of this batch, never model output.
    request = store.get_integration_request(f"ir-{work_id}")
    assert request is not None and request.status == "open"
    assert store.supervisor_event_cursor() == store.supervisor_event_head()
    assert store.get_supervisor_decision("d-int")["decision_kind"] == (
        "integration_request")


def test_integration_retry_reuses_the_harness_request_id(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)
    _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    # First attempt names an unknown donor: rejected, nothing created.
    submitter.write_decision(work_id, {
        "decision_id": "d-bad", "decision_kind": "integration_request",
        "node_ids": [], "rationale": "matured.",
        "detail": {
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["ghost"],
            "selection_rationale": "unknown donor",
        },
        "event_cursor_to": store.supervisor_event_head(),
    })
    scheduler.step()
    assert store.latest_scheduler_event(
        "supervisor_decision_rejected")["work_id"] == work_id
    assert store.integration_requests() == []

    # The same batch retries under the same work id, hence the same
    # harness-assigned request id: exactly one request ever appears.
    scheduler.step()
    retry_work_id, _ = submitter.supervisor[-1]
    assert retry_work_id == work_id
    submitter.write_decision(work_id, {
        "decision_id": "d-good", "decision_kind": "integration_request",
        "node_ids": [], "rationale": "matured.",
        "detail": {
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["exp-donor"],
            "selection_rationale": "complementary validated results",
        },
        "event_cursor_to": store.supervisor_event_head(),
    })
    scheduler.step()

    requests = store.integration_requests()
    assert [r.integration_request_id for r in requests] == [
        f"ir-{work_id}"]


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


class _DeadOnStartupSubmitter(Submitter):
    presumes_dead_on_startup = True


def test_restart_redelivers_unconsumed_batch_to_same_work(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = _DeadOnStartupSubmitter(tmp_path)
    first = _scheduler(store, tmp_path, submitter)
    first.step()
    assert [work_id for work_id, _ in submitter.supervisor] == [
        "supervisor-1"]

    # Process death: a fresh scheduler presumes the running attempt lost.
    second = _scheduler(store, tmp_path, submitter)
    second.step()
    assert [work_id for work_id, _ in submitter.supervisor] == [
        "supervisor-1", "supervisor-1"]

    # The redelivered batch commits exactly once.
    submitter.write_decision("supervisor-1", {
        "decision_id": "d-restart", "decision_kind": "growth",
        "seat_purchases": [{"node_id": root.node_id, "lens": "G5"}],
        "rationale": "second delivery decides.",
        "detail": {}, "event_cursor_to": 1,
    })
    second.step()
    assert store.supervisor_event_cursor() == 1
    assert len(store.open_allocations()) == 1


def test_pending_events_block_quiescence(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)
    scheduler.stop_allocating = True

    store.emit_supervisor_event("budget_changed", {"remaining_usd": 0.0})

    assert store.pending_supervisor_events()
    assert scheduler._quiescent() is False


def test_stale_integration_decision_has_zero_side_effects(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)
    cursor = _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    # New evidence lands while the worker is thinking.
    store.emit_supervisor_event(
        "lease_terminal",
        {"allocation_id": "a1", "node_id": root.node_id,
         "outcome": "abstain"})
    submitter.write_decision(work_id, {
        "decision_id": "d-int", "decision_kind": "integration_request",
        "node_ids": [], "rationale": "branches matured.",
        "detail": {
            "target_node_id": root.node_id,
            "donor_experiment_ids": ["exp-donor"],
            "selection_rationale": "complementary validated results",
        },
        "event_cursor_to": store.supervisor_event_head() - 1,
    })
    scheduler.step()

    # The stale decision left nothing behind: no request (under the id the
    # harness would have assigned), no decision row, no cursor advance, no
    # accepted/created events (design §9).
    assert store.get_integration_request(f"ir-{work_id}") is None
    assert store.get_supervisor_decision("d-int") is None
    assert store.supervisor_event_cursor() == cursor
    assert store.supervisor_event_head() == cursor + 2
    assert store.latest_scheduler_event("supervisor_decision_stale")[
        "work_id"] == work_id
    assert store.latest_scheduler_event(
        "integration_request_created") is None


def _seed_reviewable_request(store, root, episode):
    """A submitted integration request with a gate-passed candidate."""
    _seed_donor(store, root, episode)
    epoch = store.current_epoch()
    store.create_integration_request(
        integration_request_id="req-1", epoch_id=epoch.epoch_id,
        target_node_id=root.node_id,
        donor_experiment_ids=("exp-donor",),
        selection_rationale="branches matured",
    )
    with store.transaction() as tx:
        tx.create_proposal(Proposal(
            proposal_id="p-int", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="integrate",
            rationale={}, status="done", created_at=0,
        ))
        tx.create_experiment(
            experiment_id="exp-int", proposal_id="p-int",
            parent_node_id=root.node_id, status="running")
    store.finish_integration_request(
        "req-1", status="submitted", proposal_id="p-int",
        experiment_id="exp-int")
    store.ingest_experiment_result(
        experiment_id="exp-int", result_sha="int-sha",
        metrics={}, gate_result=_gate(True), status="completed")


def test_stale_epoch_review_has_zero_side_effects(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_reviewable_request(store, root, episode)
    cursor = _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    store.emit_supervisor_event(
        "lease_terminal",
        {"allocation_id": "a1", "node_id": root.node_id,
         "outcome": "abstain"})
    submitter.write_decision(work_id, {
        "decision_id": "d-epoch", "decision_kind": "epoch_review",
        "node_ids": [], "rationale": "candidate covers donors.",
        "detail": {
            "integration_request_id": "req-1",
            "review": "promote",
        },
        "event_cursor_to": store.supervisor_event_head() - 1,
    })
    scheduler.step()

    # No promotion, no closure, no decision row, cursor untouched.
    assert store.current_epoch().epoch_id == "epoch-0"
    assert store.get_integration_request("req-1").status == "submitted"
    assert store.get_supervisor_decision("d-epoch") is None
    assert store.supervisor_event_cursor() == cursor
    assert store.latest_scheduler_event("supervisor_decision_stale")[
        "work_id"] == work_id
    assert store.latest_scheduler_event("epoch_promoted") is None


def test_capped_run_harvests_supervisor_result_without_applying(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, _, _ = _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)

    scheduler.step()
    work_id, _ = submitter.supervisor[0]
    # The driver hits its eval/budget cap while the gate worker is thinking.
    scheduler.stop_allocating = True
    submitter.write_decision(work_id, {
        "decision_id": "d1", "decision_kind": "growth",
        "node_ids": [root.node_id], "rationale": "made under the old budget.",
        "detail": {}, "event_cursor_to": 1,
    })
    scheduler.step()

    # The result is harvested: the attempt closes and the discard is visible.
    assert store.running_attempts("supervisor") == []
    assert store.latest_scheduler_event("supervisor_decision_discarded")[
        "work_id"] == work_id
    # But it is applied to nothing: no leases, no decision row, cursor
    # unconsumed — the judgment cannot derive work under the new budget.
    assert store.get_supervisor_decision("d1") is None
    assert store.supervisor_event_cursor() == 0
    assert store.open_allocations() == []
    assert store.pending_supervisor_events()
    # Not a failure, not a retry: the worker did its job, nothing resubmits.
    attempts = store.attempts_for_work(work_id, "supervisor")
    assert [item.status for item in attempts] == ["succeeded"]
    assert store.latest_scheduler_event("supervisor_stalled") is None
    assert len(submitter.supervisor) == 1
    # The bounded driver's exit condition is reachable again...
    assert scheduler._in_flight() is False
    # ...and run() parks as capped instead of spinning on pending evidence.
    outcome = scheduler.run(max_steps=5)
    assert outcome["status"] == "capped"


def _open_request(store, root):
    epoch = store.current_epoch()
    store.create_integration_request(
        integration_request_id="req-open", epoch_id=epoch.epoch_id,
        target_node_id=root.node_id,
        donor_experiment_ids=("exp-donor",),
        selection_rationale="branches matured",
    )


def test_capped_run_starts_no_new_gate_or_integrator_work(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)
    _open_request(store, root)
    _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)
    scheduler.stop_allocating = True

    # Pending evidence and an open request both exist; a capped run must
    # start neither a gate turn nor an integrator job.
    store.emit_supervisor_event("budget_changed", {"remaining_usd": 0.0})
    scheduler.step()

    assert submitter.supervisor == []
    assert submitter.integrator == []
    assert submitter.proposer == []
    assert store.get_integration_request("req-open").status == "open"


def test_capped_run_still_harvests_running_integrator(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)
    _open_request(store, root)
    _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter)
    # An integrator job already in flight when the cap lands.
    store.prepare_integration_request("req-open")
    store.record_attempt(
        logical_work_id="req-open", kind="integrator",
        status="running", started_at=0.0)
    submitter.write_integrator_result("req-open", {
        "outcome": "abstained", "reason": "donors conflict",
    })
    scheduler.stop_allocating = True

    telemetry = scheduler.step()

    # In-flight work is drained to completion, not abandoned mid-run.
    assert telemetry["integrated"] == 1
    assert store.get_integration_request("req-open").status == "abstained"
    assert store.running_attempts("integrator") == []


def test_baseline_mode_allocates_nothing_when_capped(tmp_path: Path):
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
    scheduler.stop_allocating = True

    scheduler._allocate_proposers(Frontier({root.node_id}, {}))

    assert submitted == []
    assert store.open_allocations() == []


def test_budget_limits_are_durable_and_change_events_are_real(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _seed(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter,
        max_terminal_evals=5, budget_usd=2.0)

    scheduler.step()
    # The wake payload carries the configured budget facts.
    _, payload = submitter.supervisor[0]
    assert payload["runtime_facts"]["max_terminal_evals"] == 5
    assert payload["runtime_facts"]["budget_usd"] == 2.0
    # First install: rows written, but constructing a run is not a budget
    # intervention — only root_ready is on the log.
    assert store.run_limits() == {
        "max_terminal_evals": 5, "budget_usd": 2.0}
    assert [e.type for e in store.pending_supervisor_events()] == [
        "root_ready"]
    # Restart-equivalent: syncing the same configuration stays silent.
    scheduler.step()
    assert store.supervisor_event_head() == 1

    # A driver-side budget change (a resumed run with different limits) is
    # a durable wake event.
    restarted = _scheduler(
        store, tmp_path, submitter,
        max_terminal_evals=5, budget_usd=1.0)
    restarted.step()
    pending = store.pending_supervisor_events()
    assert pending[-1].type == "budget_changed"
    assert pending[-1].payload["budget_usd"] == 1.0
    assert pending[-1].payload["changed"] == ["budget_usd"]

    # The durable state survives a process restart (a fresh store reads it).
    reopened = ResearchStore(tmp_path / "state.db")
    assert reopened.run_limits()["budget_usd"] == 1.0


def test_durable_eval_cap_blocks_new_work_on_restart(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)  # one terminal experiment
    # A previous process installed the limit; the eval budget is spent.
    store.install_run_limits(
        {"max_terminal_evals": 1, "budget_usd": None})
    _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = _scheduler(store, tmp_path, submitter, max_terminal_evals=1)

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()

    # The restart's very first step starts nothing despite pending
    # evidence — the durable cap is derived before any new work.
    assert submitter.supervisor == []
    assert submitter.proposer == []
    assert submitter.integrator == []
    assert scheduler._allocation_disabled() is True
    assert store.pending_supervisor_events()  # batch stays unconsumed
    # A plain run() parks as capped without any driver-side flag.
    assert scheduler.run(max_steps=4)["status"] == "capped"


def test_durable_budget_cap_blocks_allocation(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _seed(store)
    # The usage ledger already exceeds the budget.
    (tmp_path / "telemetry").mkdir(parents=True, exist_ok=True)
    (tmp_path / "telemetry" / "usage.jsonl").write_text(
        '{"input_tokens": 1000000, "output_tokens": 0}\n',
        encoding="utf-8",
    )
    store.install_run_limits(
        {"max_terminal_evals": None, "budget_usd": 0.5})
    _sync_cursor(store)
    submitter = Submitter(tmp_path)
    scheduler = Scheduler(
        store, tmp_path,
        SchedulerConfig(
            quiescence_window_proposals=1, budget_usd=0.5),
        evolution_config=EvolutionConfig(
            goal="faster", repo_path=tmp_path,
            runtime_image=tmp_path / "apptainer.sif",
            editable_paths=(), frozen_paths=(), eval_commands=(),
            metrics_schema={}, axes=(),
            pricing={"input_usd_per_1m": 0.67, "output_usd_per_1m": 2.02},
        ),
        submitter=submitter,
    )

    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    scheduler.step()

    # $0.67 spent against a $0.50 budget: allocation is disabled, not just
    # reported.
    assert scheduler._allocation_disabled() is True
    assert submitter.supervisor == []
    assert store.pending_supervisor_events()


class _GoneSubmitter(Submitter):
    """Backend probe that reports every missing-result job as gone."""

    def probe_job(self, work_id: str, work_kind: str) -> str:
        return "gone"

    def remove_job(self, work_id: str, work_kind: str) -> None:
        return None


def _write_work_manifest(
    run_dir: Path, directory: str, work_id: str,
) -> None:
    path = run_dir / directory / work_id / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"payload": {"attempt_id": "old"}}))


def test_durable_cap_blocks_reconcile_resubmissions(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    _seed_donor(store, root, episode)  # terminal evals == cap == 1
    _open_request(store, root)
    store.prepare_integration_request("req-open")
    store.install_run_limits(
        {"max_terminal_evals": 1, "budget_usd": None})
    _sync_cursor(store)
    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    work_id = f"supervisor-{store.supervisor_event_head()}"
    store.record_attempt(
        logical_work_id=work_id, kind="supervisor",
        status="running", started_at=0.0)
    _write_work_manifest(
        tmp_path, "supervisor_decisions", work_id)
    store.record_attempt(
        logical_work_id="req-open", kind="integrator",
        status="running", started_at=0.0)
    _write_work_manifest(
        tmp_path, "integration_requests", "req-open")
    submitter = _GoneSubmitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_terminal_evals=1)

    scheduler.step()

    assert scheduler._allocation_disabled() is True
    assert submitter.supervisor == []
    assert submitter.integrator == []
    assert [
        attempt.status
        for attempt in store.attempts_for_work(work_id, "supervisor")
    ] == ["failed"]
    assert [
        attempt.status
        for attempt in store.attempts_for_work("req-open", "integrator")
    ] == ["failed"]


def test_terminal_ingest_caps_before_reconcile_resubmission(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    root, episode, _ = _seed(store)
    store.emit_supervisor_event(
        "root_ready", {"root_node_id": root.node_id})
    _sync_cursor(store)
    with store.transaction() as tx:
        tx.create_proposal(Proposal(
            proposal_id="p-cap", node_id=root.node_id,
            episode_id=episode.episode_id, instruction="finish",
            rationale={}, status="running", created_at=0,
        ))
        tx.create_experiment(
            experiment_id="exp-cap", proposal_id="p-cap",
            parent_node_id=root.node_id, status="running")
    store.record_attempt(
        logical_work_id="exp-cap", kind="experiment",
        status="running", started_at=0.0)
    result_path = tmp_path / "experiments" / "exp-cap" / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({
        "status": "completed",
        "result": {
            "outcome": "COMPLETED", "sha": "cap-child",
            "metrics": {}, "gate": {"passed": True, "results": {}},
            "changed_paths": [],
        },
    }))
    store.install_run_limits(
        {"max_terminal_evals": 1, "budget_usd": None})
    store.emit_supervisor_event("goal_changed", {"goal": "faster"})
    work_id = f"supervisor-{store.supervisor_event_head()}"
    store.record_attempt(
        logical_work_id=work_id, kind="supervisor",
        status="running", started_at=0.0)
    _write_work_manifest(
        tmp_path, "supervisor_decisions", work_id)
    submitter = _GoneSubmitter(tmp_path)
    scheduler = _scheduler(
        store, tmp_path, submitter, max_terminal_evals=1)

    scheduler.step()

    assert scheduler._queries.terminal_experiment_count() == 1
    assert scheduler._allocation_disabled() is True
    assert submitter.supervisor == []
