"""Context assembly helpers for SimpleEvolution proposer episodes."""
from __future__ import annotations

import json
from typing import Any


def build_first_layer_pack(layer: dict[str, Any]) -> str:
    """Render a Child seat's first layer: facts + handover, nothing else.

    科学家完整研究制 §2.6 — inheritance is re-authoring, not forwarding:
    the predecessor's research-state BODY never crosses (belief stays
    signed and pull-only); verified evidence graduates into the fact block
    with the signature stripped; the handover is the only pushed prose, a
    dead-end map written to a successor wearing a different lens.
    """
    if not layer:
        return ""
    lines = [
        "You are newly assigned to this Child world. You inherit the "
        "objective project — facts and one handover map — not the "
        "predecessor's cognition. Their full record is pull-only (ids at "
        "the end); read it if and when YOU judge it worth a look.",
        "Current Child Node — authoritative Harness facts:",
        json.dumps(layer.get("child_node", {}), ensure_ascii=False,
                   sort_keys=True),
        "Adjudication of this world — authoritative Harness facts:",
        json.dumps(layer.get("adjudication", {}), ensure_ascii=False,
                   sort_keys=True),
    ]
    graduated = layer.get("graduated_evidence") or []
    if graduated:
        lines.append(
            "Graduated evidence — facts verified by adjudication "
            "(unsigned; they are the world's state, not an opinion):"
        )
        lines.append(json.dumps(graduated, ensure_ascii=False,
                                sort_keys=True))
    handover = layer.get("handover")
    if isinstance(handover, dict):
        compliant = layer.get("handover_compliant", True)
        lines.append(
            "Handover from the seat that built this world — a MAP for a "
            "successor wearing a DIFFERENT lens (dead ends, open doors, "
            "one warning), not their worldview and not an instruction"
            + ("." if compliant else
               " (NOTE: this handover missed the harness format bar — "
               "treat it with extra suspicion).")
            + ":"
        )
        lines.append(json.dumps(handover, ensure_ascii=False,
                                sort_keys=True))
    pull = layer.get("pull", {})
    if pull:
        lines.append(
            "Pull channel (ids only — fetch deliberately, never "
            "forwarded): " + json.dumps(pull, ensure_ascii=False,
                                        sort_keys=True)
        )
    return "\n".join(lines)


def build_world_transition_pack(transition: dict[str, Any]) -> str:
    """Format an experiment result as a world-transition message.

    ``transition`` comes from the Scheduler and contains the parent/child facts
    (metrics, gate, diff).  This text is injected into the seat's resume
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
