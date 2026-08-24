"""Tests for eval command metric parsing."""
from __future__ import annotations

from simpleevo.contracts import ProcessResult
from simpleevo.adjudicate.evaluator import _parse_metrics


def test_parse_objective_and_gates():
    text = """
Some header
TOTAL_MS=123.45
CORRECT=pass
FAST=yes
"""
    schema = {
        "objective": {"key": "TOTAL_MS"},
        "gates": [
            {"key": "CORRECT"},
            {"key": "FAST"},
        ],
    }
    metrics = _parse_metrics(text, schema)
    assert metrics == {"TOTAL_MS": 123.45, "CORRECT": True, "FAST": True}


def test_parse_gate_fail():
    text = "CORRECT=fail"
    schema = {"objective": {"key": "X"}, "gates": [{"key": "CORRECT"}]}
    metrics = _parse_metrics(text, schema)
    assert metrics == {"CORRECT": False}


def test_parse_missing_key_is_omitted():
    text = "TOTAL_MS=1.0"
    schema = {
        "objective": {"key": "TOTAL_MS"},
        "gates": [{"key": "MISSING"}],
    }
    metrics = _parse_metrics(text, schema)
    assert metrics == {"TOTAL_MS": 1.0}
