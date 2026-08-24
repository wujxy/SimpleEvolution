"""Tests for the deterministic diff gate and metric gate.

The editable-set enforcement is mount-layer (the executor's world is RO
outside the rw overlays), so there is no post-hoc path gate to test here;
``paths_allowed`` remains only as a validation helper for callers that
want a belt-and-braces assertion.
"""
from __future__ import annotations

from simpleevo.contracts import EvaluationResult, GateResult
from simpleevo.adjudicate.gate import GateSpec, apply_gates, paths_allowed


def test_paths_allowed_under_editable_prefix():
    editable = ["src/", "README.md"]
    assert paths_allowed({"src/foo.py", "README.md"}, editable)


def test_paths_allowed_rejects_non_editable():
    editable = ["src/"]
    assert not paths_allowed({"src/foo.py", "Makefile"}, editable)


def test_apply_gates_passes_when_all_true():
    evaluation = EvaluationResult(
        text="OK",
        metrics={"correct": True, "fast": True},
        returncodes=(0,),
    )
    spec = GateSpec("OBJECTIVE", ("correct", "fast"))
    gate = apply_gates(evaluation, spec)
    assert gate.passed is True
    assert gate.results["correct"].passed is True
    assert gate.results["fast"].passed is True


def test_apply_gates_fails_on_missing_gate():
    evaluation = EvaluationResult(
        text="OK",
        metrics={"correct": False},
        returncodes=(0,),
    )
    spec = GateSpec("OBJECTIVE", ("correct", "fast"))
    gate = apply_gates(evaluation, spec)
    assert gate.passed is False
    assert gate.results["fast"].passed is None


def test_apply_gates_ignores_objective_for_validity():
    evaluation = EvaluationResult(
        text="OK",
        metrics={"OBJECTIVE": 123.0, "correct": True},
        returncodes=(0,),
    )
    spec = GateSpec("OBJECTIVE", ("correct",))
    gate = apply_gates(evaluation, spec)
    assert gate.passed is True
