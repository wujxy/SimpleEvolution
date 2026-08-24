"""Admission validation for agent-originated write requests.

Mechanical checks only — never judges research compatibility.  Lives in the
harness (not in the agent packages) because it guards the shared ledger.
"""
from __future__ import annotations

from typing import Any

from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import ResearchStore


def validate_integration_request(
    store: ResearchStore,
    epoch_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Mechanically validate a Supervisor request; never judge compatibility."""
    request_id = str(raw.get("integration_request_id", "")).strip()
    target_id = str(raw.get("target_node_id", "")).strip()
    rationale = str(raw.get("selection_rationale", "")).strip()
    donors = tuple(str(item) for item in raw.get("donor_experiment_ids", ()))
    if not request_id or not target_id or not rationale or not donors:
        raise ValueError("incomplete integration request")
    if len(set(donors)) != len(donors):
        raise ValueError("integration donors must be unique")
    queries = ResearchQueries(store.path)
    if queries.get_node(target_id) is None:
        raise ValueError("unknown integration target")
    for experiment_id in donors:
        experiment = queries.get_experiment(experiment_id)
        if (
            experiment is None
            or experiment.status != "completed"
            or not experiment.gate_result.passed
            or not experiment.child_node_id
        ):
            raise ValueError("integration donors must be gate-passed experiments")
    return {
        "integration_request_id": request_id,
        "epoch_id": epoch_id,
        "target_node_id": target_id,
        "donor_experiment_ids": donors,
        "selection_rationale": rationale,
    }
