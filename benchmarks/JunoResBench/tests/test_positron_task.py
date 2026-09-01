"""Scoring-contract tests for the IBD-like positron task."""

import importlib.util
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tasks" / "ibd_positron_multisite" / "evaluator" / "scoring.py"
SPEC = importlib.util.spec_from_file_location("positron_scoring", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
score_positron = MODULE.score_positron


def _probe_predictions(seed=11, events_per_probe=4000):
    rng = np.random.default_rng(seed)
    kinetic = np.repeat(np.array([0, .5, 1, 2, 3, 4, 5, 6, 8, 11.]), events_per_probe)
    visible = kinetic + 1.022
    relative = np.sqrt(.015**2 / visible + .002**2 + .004**2 / visible**2)
    return kinetic, visible + rng.normal(size=len(visible)) * visible * relative


def test_positron_rejects_affine_energy_scale_loophole():
    kinetic, prediction = _probe_predictions()
    control_truth = np.linspace(1.022, 12.022, 6400)

    score = score_positron(kinetic, prediction, control_truth, 1.25 * control_truth)

    assert score["valid"] is False
    assert any("global energy scale" in reason for reason in score["invalid_reasons"])
