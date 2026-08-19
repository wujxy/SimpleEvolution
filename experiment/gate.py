"""Pure hard-gate decision for an experiment evaluation."""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import EvaluationResult, GateDecision, GateResult


PATHS = "PATHS"
EVAL_COMMANDS = "EVAL_COMMANDS"


def gate_block(metrics_schema: dict | None) -> str:
    """Render declared gate descriptions for prompts."""
    if not metrics_schema:
        return ""
    described = [
        gate for gate in (metrics_schema.get("gates") or [])
        if gate.get("description")
    ]
    return "\n".join(
        f"- {gate['key']}: {gate['description']}" for gate in described
    )


@dataclass(frozen=True)
class GateSpec:
    objective_key: str
    gate_keys: tuple[str, ...]


def apply_gates(
    evaluation: EvaluationResult | None,
    spec: GateSpec,
    *,
    skip_reason: str | None = None,
) -> GateDecision:
    """Return a GateDecision from an evaluation result.

    The objective value is ignored for gate validity: gate.passed only reflects
    command success and declared gate booleans.  Objective quality is a separate
    concern handled by the scheduler/frontier layer.
    """
    if spec.objective_key in {PATHS, EVAL_COMMANDS}:
        raise ValueError(
            f"objective key {spec.objective_key} is reserved by the harness"
        )
    seen = {PATHS, EVAL_COMMANDS, spec.objective_key}
    for key in spec.gate_keys:
        if key in seen:
            raise ValueError(f"gate key {key} is reserved or duplicated")
        seen.add(key)

    if evaluation is None:
        reason = skip_reason or "not run"
        rows = {
            PATHS: GateResult(True),
            EVAL_COMMANDS: GateResult(None, reason),
        }
        rows.update({key: GateResult(None, reason) for key in spec.gate_keys})
        return GateDecision(rows, False)

    commands_ok = (
        evaluation.error is None
        and all(code == 0 for code in evaluation.returncodes)
    )
    command_detail = evaluation.error or (
        "" if commands_ok else f"exit codes: {list(evaluation.returncodes)}"
    )
    rows = {
        PATHS: GateResult(True),
        EVAL_COMMANDS: GateResult(commands_ok, command_detail),
    }
    for key in spec.gate_keys:
        value = evaluation.metrics.get(key)
        detail = (
            ""
            if value is True
            else "evaluator reported FAIL"
            if value is False
            else "metric missing or unknown"
        )
        rows[key] = GateResult(
            value if isinstance(value, bool) else None,
            detail,
        )
    passed = all(row.passed is True for row in rows.values())
    return GateDecision(rows, passed)


def paths_allowed(changed_paths: set[str], editable_paths: list[str]) -> bool:
    """Hard gate: every changed path must be under an editable prefix."""
    for path in changed_paths:
        if not any(
            path == editable or path.startswith(editable.rstrip("/") + "/")
            for editable in editable_paths
        ):
            return False
    return True
