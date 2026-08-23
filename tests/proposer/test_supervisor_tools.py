"""Read-only investigation tools for the persistent Supervisor."""
from __future__ import annotations

from pathlib import Path

from proposer.l2_memory import L2MemoryService
from proposer.supervisor import SupervisorTools
from simpleevo.db.store import GateDecision, GateResult, ResearchStore


def _gate(passed: bool = True) -> GateDecision:
    return GateDecision({"PASS": GateResult(passed, "")}, passed)


def _seed(tmp_path: Path) -> tuple[L2MemoryService, ResearchStore, dict]:
    store = ResearchStore(tmp_path / "simpleevo.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="st-root",
            metrics={"score": 10.0}, gate_result=_gate(), depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None, node_id=root.node_id)
        dormant = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-d", sha="st-dorm",
            metrics={"score": 1.0}, gate_result=_gate(), depth=1,
            status="dormant",
        )
        dead = tx.create_node(
            parent_node_id=root.node_id, experiment_id="exp-x", sha="st-dead",
            metrics={}, gate_result=_gate(), depth=1, status="dead",
        )
        proposal = tx.create_proposal(_proposal(root.node_id, episode.episode_id))
        experiment = tx.create_experiment(
            proposal_id=proposal.proposal_id, parent_node_id=root.node_id)

    memory = L2MemoryService(tmp_path, db_path=store.path)
    facts = {
        "max_proposer_inflight": 2,
        "max_experiment_inflight": 2,
        "proposal_slots": 3,
        "max_research_per_node": 3,
        "max_proposals_per_node": 9,
    }
    return memory, store, {
        "root": root, "episode": episode, "dormant": dormant, "dead": dead,
        "proposal": proposal, "experiment": experiment, "facts": facts,
    }


def _proposal(node_id: str, episode_id: str):
    from simpleevo.db.store import Proposal

    return Proposal(
        proposal_id="st-prop-1", node_id=node_id, episode_id=episode_id,
        instruction="cache the metric", rationale={}, status="queued",
        created_at=0.0,
    )


def _tools(memory: L2MemoryService, facts: dict) -> SupervisorTools:
    return SupervisorTools(memory, runtime_facts=facts)


def test_node_evidence_actions_dispatch(tmp_path: Path):
    memory, _, world = _seed(tmp_path)
    tools = _tools(memory, world["facts"])

    node = tools.execute({"action": "inspect_node",
                          "node_id": world["root"].node_id})
    assert node["node_id"] == world["root"].node_id
    assert world["dormant"].node_id in node["children"]

    compared = tools.execute({
        "action": "compare_nodes",
        "node_ids": [world["root"].node_id, world["dormant"].node_id],
    })
    assert len(compared["nodes"]) == 2

    lineage = tools.execute({
        "action": "lineage", "node_id": world["dormant"].node_id})
    assert [n["node_id"] for n in lineage["path"]] == [
        world["root"].node_id, world["dormant"].node_id,
    ]

    found = tools.execute({
        "action": "search_experiments", "query": "metric",
        "buckets": False})
    assert found["results"] == [] or all(
        row["experiment_id"] == world["experiment"].experiment_id
        for row in found["results"]
    )


def test_experiment_and_memo_two_step_discipline(tmp_path: Path):
    memory, _, world = _seed(tmp_path)
    tools = _tools(memory, world["facts"])
    experiment_id = world["experiment"].experiment_id

    blocked = tools.execute({
        "action": "inspect_originating_research_state",
        "experiment_id": experiment_id,
    })
    assert blocked.get("ok") is False

    inspected = tools.execute({
        "action": "inspect_experiment", "experiment_id": experiment_id})
    assert inspected["experiment_id"] == experiment_id

    memo = tools.execute({
        "action": "inspect_originating_research_state",
        "experiment_id": experiment_id,
    })
    # The proposal carries no research state in this fixture; the point is
    # that the gate opened rather than refusing access.
    assert memo.get("ok") is not False or "research memo unavailable" in (
        memo.get("error") or "")


def test_list_nodes_shows_all_history_with_mechanical_flag(tmp_path: Path):
    memory, store, world = _seed(tmp_path)
    tools = _tools(memory, world["facts"])
    with store.transaction() as tx:
        dormant_episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=world["dormant"].node_id,
        )
    store.allocate_proposer(
        node_id=world["dormant"].node_id,
        episode_id=dormant_episode.episode_id,
        proposal_slots=1,
    )

    listing = tools.execute({"action": "list_nodes"})
    by_id = {row["node_id"]: row for row in listing["nodes"]}
    assert set(by_id) == {
        world["root"].node_id, world["dormant"].node_id, world["dead"].node_id,
    }
    assert by_id[world["dead"].node_id]["allocatable"] is False
    # Seat semantics: an open seat does NOT make a node unpurchasable —
    # another lens on the same node is a legal concurrent purchase.
    assert by_id[world["dormant"].node_id]["allocatable"] is True
    assert by_id[world["dormant"].node_id]["seats_inflight"] == 1
    assert by_id[world["root"].node_id]["allocatable"] is True
    assert "ranking" not in listing
    assert "recommended" not in listing


def test_node_allocations_and_run_status(tmp_path: Path):
    memory, store, world = _seed(tmp_path)
    allocation = store.allocate_proposer(
        node_id=world["root"].node_id,
        episode_id=world["episode"].episode_id,
        proposal_slots=2,
    )

    tools = _tools(memory, world["facts"])
    history = tools.execute({
        "action": "inspect_node_allocations",
        "node_id": world["root"].node_id,
    })
    assert [a["allocation_id"] for a in history["allocations"]] == [
        allocation.allocation_id]
    assert history["allocations"][0]["decision_id"] is None
    assert any(
        e["experiment_id"] == world["experiment"].experiment_id
        for e in history["experiments"]
    )

    status = tools.execute({"action": "inspect_run_status"})
    assert status["config"]["max_proposer_inflight"] == 2
    assert status["running_attempts"] == {}
    assert status["open_allocations"] == 1
    assert status["node_counts"]["active"] == 1


def test_execute_never_raises(tmp_path: Path):
    memory, _, world = _seed(tmp_path)
    tools = _tools(memory, world["facts"])

    assert tools.execute({"action": "nope"}).get("ok") is False
    assert tools.execute({"action": "inspect_node"}).get("ok") is False
    assert tools.execute({"action": "inspect_node", "node_id": "ghost"}) == {
        "ok": False, "error": "node not found: ghost",
    }


def test_inspect_run_status_reports_durable_budget(tmp_path: Path):
    memory, store, world = _seed(tmp_path)
    # A completed experiment consumes one terminal eval.
    store.ingest_experiment_result(
        experiment_id=world["experiment"].experiment_id,
        result_sha="done", metrics={"score": 2.0},
        gate_result=_gate(), status="completed",
    )
    store.install_run_limits(
        {"max_terminal_evals": 3, "budget_usd": 1.0})
    (tmp_path / "telemetry").mkdir(parents=True, exist_ok=True)
    (tmp_path / "telemetry" / "usage.jsonl").write_text(
        '{"input_tokens": 1000000, "output_tokens": 500000}\n',
        encoding="utf-8",
    )
    tools = _tools(memory, {
        **world["facts"],
        "pricing": {
            "input_usd_per_1m": 0.67, "output_usd_per_1m": 2.02,
        },
    })

    status = tools.execute({"action": "inspect_run_status"})

    budget = status["budget"]
    assert budget["max_terminal_evals"] == 3
    assert budget["terminal_evals"] == 1
    assert budget["remaining_terminal_evals"] == 2
    # 1M input * $0.67 + 0.5M output * $2.02 = $1.68 against a $1.0 budget.
    assert budget["spend_usd"] == 1.68
    assert budget["remaining_usd"] == 0.0
    assert budget["capped"] is True


def test_run_status_without_limits_has_no_budget_block(tmp_path: Path):
    memory, _, world = _seed(tmp_path)
    tools = _tools(memory, world["facts"])

    status = tools.execute({"action": "inspect_run_status"})

    assert "budget" not in status
