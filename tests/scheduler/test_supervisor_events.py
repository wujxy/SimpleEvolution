"""Wake-event emission points for the tree-growth Supervisor gate."""
from __future__ import annotations

from pathlib import Path

from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


def _gate(passed: bool = True) -> GateDecision:
    return GateDecision({"PASS": GateResult(passed, "")}, passed)


def _seed_root(store: ResearchStore):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="evt-root",
            metrics={},
            gate_result=_gate(),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id)
    return root, episode


def test_ingest_experiment_result_emits_terminal_event(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="exp-evt-root",
            metrics={}, gate_result=_gate(), depth=0, status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id)
        proposal = tx.create_proposal(_proposal(root.node_id, episode.episode_id))
        experiment = tx.create_experiment(
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
        )

    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="child-sha",
        metrics={"score": 2.0},
        gate_result=_gate(True),
        status="completed",
    )

    (event,) = store.pending_supervisor_events()
    assert event.type == "experiment_terminal"
    assert event.payload["experiment_id"] == experiment.experiment_id
    assert event.payload["status"] == "completed"
    assert event.payload["child_node_id"] == child.node_id
    assert event.payload["gate_passed"] is True


def test_deallocate_emits_lease_terminal_only_without_proposals(
    tmp_path: Path,
):
    store = ResearchStore(tmp_path / "state.db")
    root, episode = _seed_root(store)
    quiet = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id, proposal_slots=1)
    productive = store.allocate_proposer(
        node_id=root.node_id, episode_id=episode.episode_id, proposal_slots=1)

    store.deallocate_proposer(
        allocation_id=quiet.allocation_id, proposals_produced=0)
    store.deallocate_proposer(
        allocation_id=productive.allocation_id, proposals_produced=2)

    (event,) = store.pending_supervisor_events()
    assert event.type == "lease_terminal"
    assert event.payload["allocation_id"] == quiet.allocation_id
    assert event.payload["node_id"] == root.node_id
    assert event.payload["outcome"] == "abstain"


def test_scheduler_step_emits_root_ready_once(tmp_path: Path):
    store = ResearchStore(tmp_path / "state.db")
    _seed_root(store)
    submitted: list[tuple[str, dict]] = []

    scheduler = Scheduler(
        store,
        tmp_path,
        SchedulerConfig(),
        submit_proposer=lambda allocation_id, payload: submitted.append(
            (allocation_id, payload)),
    )

    scheduler.step()

    (event,) = store.pending_supervisor_events()
    assert event.type == "root_ready"

    scheduler.step()
    events = [e for e in store.pending_supervisor_events()
              if e.type == "root_ready"]
    assert len(events) == 1


def _proposal(node_id: str, episode_id: str):
    from simpleevo.db.store import Proposal

    return Proposal(
        proposal_id="prop-evt-1",
        node_id=node_id,
        episode_id=episode_id,
        instruction="try something",
        rationale={},
        status="queued",
        created_at=0.0,
    )
