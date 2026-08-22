"""Stateless Supervisor contracts and objective group snapshot."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore


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
