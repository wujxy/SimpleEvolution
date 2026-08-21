"""Context assembly helpers for SimpleEvolution proposer episodes."""
from __future__ import annotations

import json
from typing import Any


def build_research_state_seed_pack(seed: dict[str, Any]) -> str:
    """Render a Child's proposal-specific cognitive starting point."""
    if not seed:
        return ""
    state = seed.get("originating_research_state", {})
    proposal = seed.get("proposal", {})
    experiment = seed.get("experiment", {})
    child = seed.get("child_node", {})
    return "\n".join([
        "Originating working model — Scientist judgment, not an established fact:",
        str(state.get("working_model", "")),
        "Proposal expectation — Scientist judgment:",
        str(proposal.get("expectation", "")),
        "Experiment outcome — authoritative Harness facts:",
        json.dumps(experiment, ensure_ascii=False, sort_keys=True),
        "Current Child Node — authoritative Harness facts:",
        json.dumps(child, ensure_ascii=False, sort_keys=True),
        "Re-ground in the current Child world before registering a revised ResearchState.",
    ])


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
