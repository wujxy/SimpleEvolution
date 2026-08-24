"""The Supervisor assembles its own facts at wake time.

Module contract (科学家完整研究制设计 §3): the growth-gate envelope carries
IDs and static knobs only; the worker rebuilds the batch (events + world
facts) and runtime facts from the store's read-only view at the moment of
consumption — facts only, no ranking, no recommendation (树增长设计 §6).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator
from simpleevo.scheduler.telemetry import spend_usd


def allocatable_node_facts(queries: ResearchQueries) -> list[dict[str, Any]]:
    """The mechanical decision set with measured metrics, creation order.

    Facts only — no ranking, no ordering by any quality signal (design §6).
    A node holding open seats stays purchasable for a DIFFERENT lens:
    concurrency on one node is the seat design's whole point, and the
    lineage-dedup fact (``untried``) already excludes the lenses those
    seats hold.
    """
    open_counts: dict[str, int] = {}
    for allocation in queries.open_allocations():
        open_counts[allocation.node_id] = (
            open_counts.get(allocation.node_id, 0) + 1)
    rows: list[dict[str, Any]] = []
    for node in queries.list_nodes():
        if node.status == "dead":
            continue
        rows.append({
            "node_id": node.node_id,
            "depth": node.depth,
            "status": node.status,
            "metrics": dict(node.metrics),
            "seats_inflight": open_counts.get(node.node_id, 0),
        })
    return rows


def seat_ledger_facts(queries: ResearchQueries) -> list[dict[str, Any]]:
    """Every seat ever bought, per node, with its outcome so far."""
    per_node: dict[str, list[dict[str, Any]]] = {}
    for row in queries.episode_operator_rows():
        if row["leases"] == 0:
            # An episode stamped with a lens but never leased cannot
            # happen post-commit (stamping is atomic with the
            # allocation); skip defensively rather than show a phantom.
            continue
        per_node.setdefault(row["node_id"], []).append({
            "lens": row["lens"],
            "episode_id": row["episode_id"],
            "state": "open" if row["open_leases"] else "finished",
            # Lease state machine detail for open seats: a lease parked in
            # adjudication or reopen is visible as such (the gate prices
            # "this seat's world is being judged / reworked" differently
            # from "this seat is researching").
            "lease_state": row["lease_state"],
            "reopen_count": row["reopen_count"],
            "conclusion_type": row["conclusion_type"],
            "proposals": row["proposals"],
        })
    return [
        {"node": node_id, "seats": seats}
        for node_id, seats in sorted(per_node.items())
    ]


def untried_lens_facts(
    queries: ResearchQueries, generator_basis: list[Generator],
) -> list[dict[str, Any]]:
    """Per living node, the lenses lineage-dedup still allows.

    This is the fact an empty selection is judged against: the seat
    menu is empty everywhere exactly when the program has asked every
    question its basis can buy (honest completion, seat design §2.4).
    """
    if not generator_basis:
        return []
    basis_ids = [item.id for item in generator_basis]
    burned = queries.burned_lenses()
    rows: list[dict[str, Any]] = []
    for node in queries.list_nodes():
        if node.status == "dead":
            continue
        rows.append({
            "node": node.node_id,
            "lenses": [
                lens for lens in basis_ids
                if lens not in burned.get(node.node_id, ())
            ],
        })
    return rows


def objective_gain(
    queries: ResearchQueries, row: dict[str, Any],
    obj_key: str | None, lower_is_better: bool,
) -> float | None:
    """Child-vs-parent objective improvement in percent for one row.

    Sign follows the objective's direction; None when the objective or
    the parent value is unknown (never fabricate a gain).
    """
    if not obj_key:
        return None
    child = (row["metrics"] or {}).get(obj_key)
    parent_node = (
        queries.get_node(row["parent_node_id"])
        if row["parent_node_id"] else None
    )
    parent = (
        parent_node.metrics.get(obj_key)
        if parent_node is not None else None
    )
    if (not isinstance(child, (int, float))
            or isinstance(child, bool)
            or not isinstance(parent, (int, float))
            or isinstance(parent, bool)
            or parent == 0):
        return None
    raw = (
        (parent - child) if lower_is_better else (child - parent)
    ) / abs(parent)
    return round(raw * 100.0, 2)


def lens_stats_facts(
    queries: ResearchQueries,
    generator_basis: list[Generator],
    metrics_schema: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Per lens, what its seats have produced across the program.

    Output statistics only — the reading (buy more / beware crowding)
    is the Supervisor's judgment; the harness states numbers.  Note the
    anti-monotone fact the numbers cannot show by themselves: a lens
    with the best record is also the one whose repeated purchase
    narrows the program's diversity.
    """
    seats: dict[str, int] = {}
    for row in queries.episode_operator_rows():
        if row["leases"]:
            seats[row["lens"]] = seats.get(row["lens"], 0) + 1
    objective = (metrics_schema or {}).get("objective")
    obj_key = (objective or {}).get("key")
    lower_is_better = bool((objective or {}).get("lower_is_better"))
    stats: dict[str, dict[str, Any]] = {}
    for row in queries.proposal_outcome_rows():
        lens = row["lens"]
        if lens is None:
            continue
        entry = stats.setdefault(lens, {
            "lens": lens, "proposals": 0, "gate_passed": 0,
            "best_gain": None,
        })
        entry["proposals"] += 1
        experiment_status = row["status"]
        gate_passed = bool(
            (row["gate_result"] or {}).get("passed")
        ) and experiment_status in {"completed", "no_change"}
        if experiment_status == "completed" and gate_passed:
            entry["gate_passed"] += 1
            gain = objective_gain(queries, row, obj_key, lower_is_better)
            if gain is not None:
                best = entry["best_gain"]
                if best is None or gain > best:
                    entry["best_gain"] = gain
    out = []
    for item in generator_basis:
        entry = stats.get(item.id) or {
            "lens": item.id, "proposals": 0, "gate_passed": 0,
            "best_gain": None,
        }
        entry["seats"] = seats.get(item.id, 0)
        out.append(entry)
    return out


def build_batch(
    queries: ResearchQueries,
    generator_basis: list[Generator],
    metrics_schema: dict[str, Any] | None,
    *,
    cursor_from: int,
    cursor_to: int,
    work_id: str,
) -> dict[str, Any]:
    """The wake batch: events in the hired range plus world facts.

    ``cursor_to`` bounds the event read so a worker that starts after newer
    events landed still decides on exactly the batch it was hired for —
    the scheduler's staleness commit checks the same bound.
    """
    epoch = queries.current_epoch()
    rejection = queries.scheduler_rejection_for_work(work_id)
    batch: dict[str, Any] = {
        "event_batch": {
            "cursor_from": cursor_from,
            "cursor_to": cursor_to,
            "events": [
                {
                    "event_id": item.event_id,
                    "type": item.type,
                    "payload": item.payload,
                }
                for item in queries.supervisor_events_between(
                    cursor_from, cursor_to)
            ],
        },
        "allocatable_nodes": allocatable_node_facts(queries),
        "seat_ledger": seat_ledger_facts(queries),
        "untried": untried_lens_facts(queries, generator_basis),
        "lens_stats": lens_stats_facts(
            queries, generator_basis, metrics_schema),
        "epoch": None if epoch is None else {
            "epoch_id": epoch.epoch_id,
            "root_node_id": epoch.root_node_id,
        },
    }
    if rejection:
        # A retry wakes the same session on the same unconsumed batch;
        # without the recorded reason the session cannot see why its
        # previous decision was refused (v3: capacity rejections
        # repeated until stall, blind to the cause).
        batch["previous_rejection"] = (
            "Your previous decision for this batch was rejected "
            f"by the scheduler: {rejection}. Submit a corrected decision."
        )
    return batch


def build_runtime_facts(
    queries: ResearchQueries,
    run_dir: Path,
    *,
    max_proposer_inflight: int,
    max_experiment_inflight: int,
    max_terminal_evals: int | None,
    budget_usd: float | None,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    """First-hand budget/capacity facts, computed at consumption time.

    The limits say nothing without the amounts already spent, and
    opportunity-cost reasoning needs both on every wake (same numbers
    the durable cap derives).  Capacity likewise: without the free
    count the gate cannot see the wall before hitting it (v3: 8
    capacity rejections, 2 stalls).
    """
    runtime_facts: dict[str, Any] = {
        "max_proposer_inflight": max_proposer_inflight,
        "max_experiment_inflight": max_experiment_inflight,
        # Seat semantics: a purchase is one seat; there are no
        # proposal slots to manage and no per-node research/proposal
        # caps — the budget is the boundary (seat design §2.1/§4).
        # Researching leases hold seats (the same shared query the
        # scheduler's capacity enforcement reads); a lease parked on
        # adjudication does not.
        "seats_inflight": queries.researching_open_allocation_count(),
        "max_terminal_evals": max_terminal_evals,
        "budget_usd": budget_usd,
    }
    runtime_facts["free_proposer_capacity"] = (
        max_proposer_inflight - queries.researching_open_allocation_count())
    terminal_used = queries.terminal_experiment_count()
    runtime_facts["terminal_evals_used"] = terminal_used
    if max_terminal_evals is not None:
        runtime_facts["remaining_terminal_evals"] = max(
            0, int(max_terminal_evals) - terminal_used)
    if pricing:
        # Token pricing so the worker's budget view can price the
        # run's usage ledger itself.
        runtime_facts["pricing"] = dict(pricing)
        spend = spend_usd(run_dir, pricing)
        runtime_facts["spend_usd"] = round(spend, 6)
        if budget_usd is not None:
            runtime_facts["remaining_usd"] = round(
                max(0.0, float(budget_usd) - spend), 6)
    return runtime_facts
