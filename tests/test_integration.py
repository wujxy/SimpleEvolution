"""Integration test: Scheduler drives proposer → experiment → new node."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from scientist.wake import research_state_seed
from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore
from simpleevo.db.queries import ResearchQueries
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        yield run_dir, store


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class _GateSubmitter:
    """Records submissions; supervisor decisions are hand-written."""

    presumes_dead_on_startup = False

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.supervisor: list[tuple[str, dict]] = []
        self.scientist: list[tuple[str, dict]] = []
        self.experiments: list[tuple[str, dict]] = []

    def submit_supervisor(self, work_id: str, payload: dict) -> str:
        self.supervisor.append((work_id, payload))
        return str(self.run_dir / "supervisor_decisions" / work_id / "result.json")

    def submit_proposer(self, allocation_id: str, payload: dict) -> str:
        self.scientist.append((allocation_id, payload))
        return str(self.run_dir / "proposer_allocations" / allocation_id / "result.json")

    def submit_experiment(self, experiment_id: str, payload: dict) -> str:
        self.experiments.append((experiment_id, payload))
        return str(self.run_dir / "experiments" / experiment_id / "result.json")

    def submit_integrator(self, request_id: str, payload: dict) -> str:
        return str(self.run_dir / "integration_requests" / request_id / "result.json")

    def decide(self, *, purchases=(), rationale="", decision_id="d",
               decision_kind="growth", detail=None):
        work_id, payload = self.supervisor[-1]
        _write_json(self.run_dir / "supervisor_decisions" / work_id / "result.json", {
            "kind": "supervisor", "request_id": work_id,
            "status": "completed",
            "result": {
                "decision_id": decision_id, "work_id": work_id,
                "decision_kind": decision_kind,
                "seat_purchases": [
                    {"node_id": node_id, "lens": lens}
                    for node_id, lens in purchases
                ],
                "rationale": rationale,
                "detail": detail or {},
                "event_cursor_to": payload["event_batch_bounds"]["cursor_to"],
            },
            "error": None, "execution": {},
        })
        return work_id


def test_scheduler_closes_proposer_experiment_loop(env):
    run_dir, store = env

    # Seed a root node and episode.
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    config = SchedulerConfig(
        max_proposer_inflight=1,
        max_experiment_inflight=1,
        frontier=FrontierConfig(axes=("total_ms",)),
        poll_seconds=0.0,
    )
    submitter = _GateSubmitter(run_dir)

    scheduler = Scheduler(store, run_dir, config, submitter=submitter)

    def write_proposer_result(allocation_id: str, payload: dict) -> None:
        state_id = f"rs-{episode.episode_id}-001"
        _write_json(
            run_dir / "proposer_allocations" / allocation_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "proposer",
                "request_id": allocation_id,
                "status": "completed",
                "result": {
                    "episode_id": episode.episode_id,
                    "node_id": root.node_id,
                    "outcome": "submit",
                    "research_states": [{
                        "research_state_id": state_id,
                        "node_id": root.node_id,
                        "episode_id": episode.episode_id,
                        "derived_from_research_state_id": None,
                        "working_model": "Repeated setup crosses the call boundary.",
                        "evidence_refs": ["source:src/fcn.cc:FCN"],
                        "created_at": 1.0,
                    }],
                    "proposals": [
                        {
                            "proposal_id": payload["proposal_ids"][0],
                            "research_state_id": state_id,
                            "instruction": "inline a small helper to reduce total_ms",
                            "rationale": {"expectation": "total_ms decreases"},
                        },
                    ],
                },
            },
        )

    def write_experiment_result(experiment_id: str) -> None:
        _write_json(
            run_dir / "experiments" / experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment_id,
                "status": "completed",
                "result": {
                    "experiment_id": experiment_id,
                    "child_node_id": None,
                    "parent_sha": "sha-root",
                    "sha": "sha-child",
                    "metrics": {"total_ms": 90.0},
                    "gate": {
                        "passed": True,
                        "results": {"PATHS": {"passed": True, "detail": ""}},
                    },
                    "outcome": "COMPLETED",
                    "eval_block": "TOTAL_MS=90.0\nPATHS=pass",
                    "changed_paths": [],
                },
            },
        )

    # Step 1: root_ready wakes the gate; the worker is submitted.
    t1 = scheduler.step()
    assert t1["proposer_jobs"] == 0
    submitter.decide(
        purchases=[(root.node_id, "G5")],
        rationale="root deserves growth through inversion.",
        decision_id="decision-1")

    # Step 2: the decision commits and the proposer lease is launched.
    t2 = scheduler.step()
    assert t2["proposer_jobs"] == 1
    allocation_id, proposer_payload = submitter.scientist[0]
    assert proposer_payload["node_id"] == root.node_id
    (allocation,) = store.open_allocations()
    assert allocation.decision_id == "decision-1"

    write_proposer_result(allocation_id, proposer_payload)

    # Step 3: the proposer result publishes (reconcile or poll ingests it)
    # and the queue drains into an experiment.
    t3 = scheduler.step()
    assert t3["experiment_jobs"] == 1
    assert submitter.experiments
    experiment_id = submitter.experiments[0][0]
    write_experiment_result(experiment_id)
    # Ingest the terminal experiment directly so the second queued proposal
    # is not drained into another experiment before the assertions below.
    assert scheduler._poll_experiments() == [experiment_id]

    # Verify child node was created.
    with store.transaction() as tx:
        children = tx._conn.execute(
            "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
        ).fetchall()
    assert len(children) == 1
    assert children[0]["sha"] == "sha-child"
    assert children[0]["metrics"] == '{"total_ms": 90.0}'
    queries = ResearchQueries(store.path)
    states = queries.research_states_for_episode(episode.episode_id)
    assert len(states) == 1
    assert queries.queued_proposals() == []
    experiment_proposal = queries.get_proposal(
        submitter.experiments[0][1]["proposal_id"])
    assert experiment_proposal.research_state_id == states[0].research_state_id
    seed = research_state_seed(
        ResearchQueries(store.path),
        queries.get_node(children[0]["node_id"]),
    )
    assert seed["originating_research_state"]["working_model"] == (
        "Repeated setup crosses the call boundary."
    )
    # The memo is signed with its author seat's lens (seat design §2.3).
    assert seed["originating_lens"] == "G5"
    assert seed["experiment"]["metrics"] == {"total_ms": 90.0}

    # The terminal experiment event re-wakes the gate for the next judgment.
    t5 = scheduler.step()
    assert t5["supervisor_pending"] == 1  # evidence awaits judgment
    assert len(submitter.supervisor) == 2
    _, wake_payload = submitter.supervisor[1]
    wake = wake_payload["event_batch_bounds"]
    assert wake["cursor_from"] == 1
    assert [
        e.type for e in ResearchQueries(store.path).supervisor_events_between(
            wake["cursor_from"], wake["cursor_to"])
    ] == ["experiment_terminal"]


def test_group_workflow_allocates_divergent_branch_and_promotes_shared_epoch(env):
    run_dir, store = env
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={"score": 10}, gate_result=GateDecision({}, True),
            depth=0, status="active",
        )
        root_episode = tx.create_episode(node_id=root.node_id)
        donor_proposal = tx.create_proposal(Proposal(
            proposal_id="donor-proposal", node_id=root.node_id,
            episode_id=root_episode.episode_id, instruction="independent win",
            rationale={}, status="running", created_at=1,
        ))
        donor_experiment = tx.create_experiment(
            experiment_id="donor-experiment",
            proposal_id=donor_proposal.proposal_id,
            parent_node_id=root.node_id, status="running",
        )
    divergent = store.ingest_experiment_result(
        experiment_id=donor_experiment.experiment_id,
        result_sha="divergent", metrics={"score": 1},
        gate_result=GateDecision({}, True), status="completed",
    )
    with store.transaction() as tx:
        tx._conn.execute(
            "UPDATE nodes SET status = 'dormant' WHERE node_id = ?",
            (divergent.node_id,),
        )

    submitter = _GateSubmitter(run_dir)
    scheduler = Scheduler(
        store, run_dir,
        SchedulerConfig(max_proposer_inflight=1, max_experiment_inflight=1),
        submitter=submitter,
    )

    # The terminal donor event wakes the gate; it funds the distinct
    # low-base lineage rather than the frontier leader.
    scheduler.step()
    submitter.decide(
        purchases=[(divergent.node_id, "G7")],
        rationale="fund the distinct low-base lineage",
        decision_id="decision-1")
    scheduler.step()
    assert submitter.scientist[0][1]["node_id"] == divergent.node_id
    (lease,) = store.open_allocations()
    assert lease.decision_id == "decision-1"

    # A separate turn requests integration — never bundled with growth.
    # (Driven through the gate directly so this test controls when the
    # Integrator is scheduled.)
    store.emit_supervisor_event("goal_changed", {"goal": "shared baseline"})
    assert scheduler._run_supervisor_gate() == []
    submitter.decide(
        decision_id="decision-2", decision_kind="integration_request",
        rationale="turn the mature branch into a shared base",
        detail={
            "target_node_id": root.node_id,
            "donor_experiment_ids": [donor_experiment.experiment_id],
            "selection_rationale": "turn the mature branch into a shared base",
        })
    assert scheduler._run_supervisor_gate() == []
    # The harness assigned the request id from the work.
    request_id = store.integration_requests("open")[0].integration_request_id
    assert request_id.startswith("ir-supervisor-")
    assert store.get_integration_request(request_id).status == "open"

    integrator_payloads = []
    scheduler.submit_integrator = lambda work_id, payload: integrator_payloads.append(payload)
    assert scheduler._schedule_integrators() == [request_id]
    payload = integrator_payloads[0]
    state_id = f"rs-{payload['episode_id']}-integration"
    _write_json(run_dir / f"integration_requests/{request_id}/result.json", {
        "status": "completed",
        "result": {
            "outcome": "submitted", "reason": None,
            "research_state": {
                "research_state_id": state_id, "node_id": root.node_id,
                "episode_id": payload["episode_id"],
                "working_model": "the donor can become the common trunk",
                "evidence_refs": ["experiment:donor-experiment"],
            },
            "proposal": {
                "proposal_id": payload["proposal_id"],
                "research_state_id": state_id,
                "instruction": "port the validated donor onto root",
                "rationale": {}, "research_operation": "synthesize",
                "donor_experiment_ids": ["donor-experiment"],
                "evidence_refs": ["experiment:donor-experiment"],
            },
        },
    })
    assert scheduler._poll_integrators() == [request_id]

    def execute(experiment_id, payload):
        _write_json(run_dir / "experiments" / experiment_id / "result.json", {
            "status": "completed",
            "result": {
                "outcome": "COMPLETED", "sha": "shared-candidate",
                "metrics": {"score": 12},
                "gate": {"passed": True, "results": {}},
                "changed_paths": [],
            },
        })

    scheduler.submit_experiment = execute
    jobs = scheduler._drain_executor_queue()
    assert scheduler._poll_experiments() == jobs

    # Epoch review is the third terminal, again through the same gate —
    # naming the existing request under review.
    scheduler.step()  # experiment_terminal wakes the gate
    submitter.decide(
        decision_id="decision-3", decision_kind="epoch_review",
        rationale="candidate passed ordinary evaluation",
        detail={
            "integration_request_id": request_id, "review": "promote",
            "rationale": "candidate passed ordinary evaluation",
            "evidence_refs": [f"experiment:{jobs[0]}"],
        })
    scheduler.step()

    assert store.current_epoch().root_node_id != root.node_id
    assert store.get_supervisor_decision("decision-3")["decision_kind"] == (
        "epoch_review")
    assert store.supervisor_event_cursor() == store.supervisor_event_head()
