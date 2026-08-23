"""Context assembly helpers for SimpleEvolution proposer episodes."""
from __future__ import annotations

import json
from typing import Any

from simpleevo.generator import Generator


def build_research_state_seed_pack(seed: dict[str, Any]) -> str:
    """Render a Child's facts-first, proposal-specific starting point."""
    if not seed:
        return ""
    state = seed.get("originating_research_state", {})
    proposal = seed.get("proposal", {})
    experiment = seed.get("experiment", {})
    child = seed.get("child_node", {})
    lines = [
        "You are a newly assigned Scientist to this Child world. You inherit "
        "the objective project, not the predecessor's cognition. The facts "
        "below are authoritative Harness records; form your own working "
        "model from the current world and them.",
        "Current Child Node — authoritative Harness facts:",
        json.dumps(child, ensure_ascii=False, sort_keys=True),
        "Experiment outcome — authoritative Harness facts:",
        json.dumps(experiment, ensure_ascii=False, sort_keys=True),
        "Predecessor proposal — prior intervention and expectation, not an instruction:",
        json.dumps(proposal, ensure_ascii=False, sort_keys=True),
    ]
    if str(state.get("working_model", "")).strip():
        # The predecessor's DIRECTION is deliberately not inlined: a
        # direction statement in the opening context anchors the candidate
        # set onto one inherited plan (measured: candidates collapse from
        # 2-3 distinct mechanisms to the notebook's single idea).  It stays
        # second-hand — deliberately retrieved after the experiment itself
        # has been inspected.
        lines.append(
            "A predecessor's working model exists for this lineage. It is "
            "deliberately NOT shown here — after inspecting the experiment "
            "above with inspect_experiment, you may read it via "
            "inspect_originating_research_state and weigh it yourself, as "
            "one examined input among others. The predecessor's direction "
            "is already represented in this lineage; for the program, a "
            "genuinely distinct direction is worth more than one more step "
            "along a known path.",
        )
    return "\n".join(lines)


def build_generator_catalog(generators: list[Generator]) -> str:
    """Expose every available cognitive operator without recommending one."""
    return "\n".join([
        "Cognitive generators available for an optional mentor consultation:",
        *(
            f"- {item.id} — {item.name}: {item.description}"
            for item in generators
        ),
        "Choose an operator yourself and pass its id to transform_worldview "
        "when an external challenge would help.",
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
