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
    metrics = transition.get("metrics", {})
    if metrics:
        lines.append("Measured metrics:")
        for key, value in metrics.items():
            lines.append(f"  {key} = {value}")
    gate = transition.get("gate", {})
    passed = gate.get("passed")
    if passed is not None:
        lines.append(f"Gate passed: {passed}")
    diff = transition.get("diff", "")
    if diff:
        lines.append("Diff summary:")
        lines.append(diff)
    return "\n".join(lines)
