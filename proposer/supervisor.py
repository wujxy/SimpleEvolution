"""Stateless Supervisor contracts and objective group snapshot."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore

from .agent_runtime import AgentRuntime
from .research_agent import AgentError, ResearchAgent


@dataclass(frozen=True)
class SnapshotNode:
    node_id: str
    parent_node_id: str | None
    experiment_id: str | None
    sha: str
    depth: int
    status: str
    metrics: dict[str, Any]


@dataclass(frozen=True)
class GroupSnapshot:
    epoch_id: str
    epoch_root_node_id: str
    watermark: str
    eligible_nodes: tuple[SnapshotNode, ...]


@dataclass(frozen=True)
class AllocationDirective:
    node_id: str
    proposal_slots: int


@dataclass(frozen=True)
class SupervisorDecision:
    decision_id: str
    epoch_id: str
    snapshot_watermark: str
    allocations: tuple[AllocationDirective, ...]
    rationale: str
    evidence_refs: tuple[str, ...]
    integration_request: dict[str, Any] | None = None


def decision_from_dict(raw: dict[str, Any]) -> SupervisorDecision:
    """Decode the durable worker artifact into the typed validation contract."""
    return SupervisorDecision(
        decision_id=str(raw["decision_id"]),
        epoch_id=str(raw["epoch_id"]),
        snapshot_watermark=str(raw["snapshot_watermark"]),
        allocations=tuple(
            AllocationDirective(
                node_id=str(item["node_id"]),
                proposal_slots=int(item["proposal_slots"]),
            )
            for item in raw["allocations"]
        ),
        rationale=str(raw["rationale"]),
        evidence_refs=tuple(str(ref) for ref in raw["evidence_refs"]),
        integration_request=raw.get("integration_request"),
    )


class SupervisorError(AgentError):
    pass


class _NoTools:
    def execute(self, action, **kwargs):  # pragma: no cover - parser forbids it
        raise SupervisorError(f"Supervisor has no tool action {action['action']!r}")


class _Session:
    """Tiny append-only session used for audit, never reused as cognition."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "session.jsonl"

    def append_message(self, role: str, content: str, *, round_id: int) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "role": role, "content": content, "round": round_id,
            }, ensure_ascii=False) + "\n")


class SupervisorAgent(ResearchAgent):
    """Stateless role that allocates attention without proposing code changes."""

    _error_class = SupervisorError

    def __init__(self, *, model, timeout_seconds: int, max_steps: int = 3):
        super().__init__(
            model=model,
            runtime=None,
            timeout_seconds=timeout_seconds,
            max_steps=max_steps,
            command_timeout_seconds=0,
            command_output_cap_chars=0,
        )

    def _parse_action(self, text: str) -> list[dict]:
        try:
            action = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SupervisorError("invalid JSON") from exc
        if not isinstance(action, dict) or action.get("action") != "submit_supervisor_decision":
            raise SupervisorError("expected submit_supervisor_decision")
        required = {
            "decision_id", "epoch_id", "snapshot_watermark",
            "allocations", "rationale", "evidence_refs",
        }
        missing = required - set(action)
        if missing:
            raise SupervisorError(f"missing decision fields: {sorted(missing)}")
        if not isinstance(action["allocations"], list):
            raise SupervisorError("allocations must be a list")
        return [action]

    def decide(
        self,
        snapshot: GroupSnapshot,
        *,
        proposer_capacity: int,
        session_dir: Path | None = None,
    ) -> SupervisorDecision:
        def run(directory: Path) -> SupervisorDecision:
            payload = asdict(snapshot)
            messages = [{"role": "user", "content": json.dumps({
                "group_snapshot": payload,
                "proposer_capacity": proposer_capacity,
            }, ensure_ascii=False)}]

            def terminal(action, state, usages, step, outcome):
                if action is None:
                    raise SupervisorError("Supervisor exhausted its step budget")
                allocations = tuple(
                    AllocationDirective(
                        node_id=str(item["node_id"]),
                        proposal_slots=int(item["proposal_slots"]),
                    )
                    for item in action["allocations"]
                )
                return SupervisorDecision(
                    decision_id=str(action["decision_id"]),
                    epoch_id=str(action["epoch_id"]),
                    snapshot_watermark=str(action["snapshot_watermark"]),
                    allocations=allocations,
                    rationale=str(action["rationale"]),
                    evidence_refs=tuple(str(ref) for ref in action["evidence_refs"]),
                    integration_request=action.get("integration_request"),
                )

            return AgentRuntime(self).run(
                system_prompt=_supervisor_prompt(),
                messages=messages,
                session=_Session(directory),
                current_round=0,
                steps_budget=self.max_steps,
                source_root=Path("."),
                build_tools=lambda scratch, home: _NoTools(),
                terminal_name="submit_supervisor_decision",
                budget_nudge="Return submit_supervisor_decision now.",
                handle_terminal=terminal,
                compact=lambda messages, usages, state: None,
                checkpoint=lambda *args, **kwargs: None,
            )

        if session_dir is not None:
            return run(Path(session_dir))
        with TemporaryDirectory(prefix="simpleevo-supervisor-") as temporary:
            return run(Path(temporary))


def _supervisor_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "supervisor.md").read_text(
        encoding="utf-8",
    )


def build_group_snapshot(
    store: ResearchStore,
    *,
    max_research_per_node: int,
    max_proposals_per_node: int,
) -> GroupSnapshot:
    """Build the Supervisor's mechanical candidate set without Frontier."""
    queries = ResearchQueries(store.path)
    epoch = store.current_epoch()
    if epoch is None:
        raise ValueError("cannot supervise a tree without an epoch root")
    open_node_ids = {item.node_id for item in store.open_allocations()}
    eligible = []
    for node in queries.list_nodes():
        if node.status == "dead" or node.node_id in open_node_ids:
            continue
        if store.count_allocations_for_node(node.node_id) >= max_research_per_node:
            continue
        if queries.proposal_count_for_node(node.node_id) >= max_proposals_per_node:
            continue
        eligible.append(SnapshotNode(
            node_id=node.node_id,
            parent_node_id=node.parent_node_id,
            experiment_id=node.experiment_id,
            sha=node.sha,
            depth=node.depth,
            status=node.status,
            metrics=dict(node.metrics),
        ))

    facts = {
        "epoch": (epoch.epoch_id, epoch.root_node_id),
        "nodes": [
            (item.node_id, item.sha, item.status, item.metrics)
            for item in eligible
        ],
        "open_allocations": sorted(open_node_ids),
        "experiments": [
            (item.experiment_id, item.status, item.result_sha)
            for item in queries.list_experiments()
        ],
    }
    watermark = hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return GroupSnapshot(
        epoch_id=epoch.epoch_id,
        epoch_root_node_id=epoch.root_node_id,
        watermark=watermark,
        eligible_nodes=tuple(eligible),
    )


def validate_decision(
    snapshot: GroupSnapshot,
    decision: SupervisorDecision,
    *,
    proposer_capacity: int,
) -> SupervisorDecision:
    if decision.epoch_id != snapshot.epoch_id:
        raise ValueError("decision belongs to another epoch")
    if decision.snapshot_watermark != snapshot.watermark:
        raise ValueError("stale supervisor decision")
    eligible = {item.node_id for item in snapshot.eligible_nodes}
    selected: set[str] = set()
    for allocation in decision.allocations:
        if allocation.node_id not in eligible:
            raise ValueError("supervisor selected an ineligible node")
        if allocation.node_id in selected:
            raise ValueError("supervisor selected a node twice")
        if allocation.proposal_slots < 1:
            raise ValueError("proposal slots must be positive")
        selected.add(allocation.node_id)
    if len(decision.allocations) > proposer_capacity:
        raise ValueError("supervisor decision exceeds proposer capacity")
    return decision
