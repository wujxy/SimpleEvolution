"""Scoring-contract tests for the single-electron task."""

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tasks" / "electron_single_site" / "evaluator"))

from scoring import parse_prediction, score_electron  # noqa: E402
from benchmarks.JunoResBench.world_generator.oracle_vertex import freeze_threshold
from benchmarks.JunoResBench.world_generator.oracle_vertex import charge_pattern_vertex_rms
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout


def _energy_fixture(seed=7, events_per_probe=4000):
    rng = np.random.default_rng(seed)
    probe = np.repeat(np.arange(1.0, 11.0), events_per_probe)
    relative = np.sqrt(0.015**2 / probe + 0.002**2 + 0.004**2 / probe**2)
    reconstructed = probe + rng.normal(size=len(probe)) * probe * relative
    controls = np.linspace(1.0, 10.0, 6400)
    return probe, reconstructed, controls


def test_electron_score_requires_energy_and_vertex_targets():
    probe, reconstructed, controls = _energy_fixture()
    truth_vertex = np.zeros((len(probe), 3))
    reconstructed_vertex = np.full((len(probe), 3), 0.20 / np.sqrt(3.0))

    score = score_electron(
        probe,
        reconstructed,
        truth_vertex,
        reconstructed_vertex,
        controls,
        controls,
        vertex_threshold_m=0.10,
    )

    assert score["energy_passed"] is True
    assert score["vertex_passed"] is False
    assert score["passed"] is False


def test_electron_prediction_requires_four_finite_scalars():
    assert parse_prediction((1.0, 0.0, 0.0, 0.0)) == (1.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="four finite"):
        parse_prediction((1.0, np.nan, 0.0))


def test_vertex_threshold_is_one_point_fifteen_times_oracle_rounded_up():
    assert freeze_threshold(0.08101) == 0.094
    assert freeze_threshold(0.08000) == 0.092


def test_charge_pattern_oracle_has_a_finite_positive_vertex_limit():
    rms = charge_pattern_vertex_rms(
        np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        PMTLayout.uniform(128),
        DetectorConfig(),
    )

    assert np.isfinite(rms)
    assert rms > 0.0
