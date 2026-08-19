"""Context assembly helpers for SimpleEvolution proposer episodes."""
from __future__ import annotations

from typing import Any


def build_world_transition_pack(transition: dict[str, Any]) -> str:
    """Format an experiment result as a world-transition message.

    ``transition`` comes from the Scheduler and contains the parent/child facts
    (metrics, gate, diff).  This text is injected into the Scientist's resume
    context as the authoritative world event.
    """
    if not transition:
        return ""
    lines = ["World transition — reality from your last experiment:"]
    parent_id = transition.get("parent_node_id")
    if parent_id:
        lines.append(f"Parent node: {parent_id}")
    experiment_id = transition.get("experiment_id")
    if experiment_id:
        lines.append(f"Experiment: {experiment_id}")
    parent_metrics = transition.get("parent_metrics", {})
    metrics = transition.get("metrics", {})
    if metrics or parent_metrics:
        lines.append("Measured metrics:")
        for key, value in parent_metrics.items():
            lines.append(f"  {key} = {value}  (before)")
        for key, value in metrics.items():
            lines.append(f"  {key} = {value}  (after)")
    gate = transition.get("gate", {})
    passed = gate.get("passed")
    if passed is not None:
        lines.append(f"Gate passed: {passed}")
    gate_results = gate.get("results", {})
    for name, result in gate_results.items():
        detail = result.get("detail", "") if isinstance(result, dict) else ""
        if detail:
            lines.append(f"  {name}: {detail}")
    diff = transition.get("diff", [])
    if diff:
        lines.append("Changed paths:")
        for path in diff:
            lines.append(f"  {path}")
    return "\n".join(lines)
