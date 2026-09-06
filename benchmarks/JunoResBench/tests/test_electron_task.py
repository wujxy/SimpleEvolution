"""Scoring-contract tests for the single-electron task."""

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tasks" / "electron_single_site" / "evaluator"))

from scoring import parse_prediction, score_electron  # noqa: E402
from evaluate import score_predictions  # noqa: E402
from benchmarks.JunoResBench.world_generator.oracle_vertex import (  # noqa: E402
    freeze_gate,
    freeze_threshold,
    charge_pattern_vertex_rms,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig  # noqa: E402
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout  # noqa: E402


def _thresholds():
    return {
        "energy_target_r_1mev": 0.03,
        "energy_resolution_gate": 0.036,
        "energy_bias_1mev_abs_max": 0.015,
        "energy_bias_abs_max": 0.02,
        "vertex_rms_reference_m": 0.092,
        "vertex_resolution_gate_m": 0.54,
        "vertex_radial_bias_abs_max_m": 0.20,
        "vertex_high_energy_rms_ratio_max": 1.20,
    }


def _energy_fixture(seed=7, events_per_probe=4000, width_scale=1.0):
    rng = np.random.default_rng(seed)
    probe = np.repeat(np.arange(1.0, 11.0), events_per_probe)
    relative = np.sqrt(0.015**2 / probe + 0.002**2 + 0.004**2 / probe**2)
    reconstructed = probe + rng.normal(size=len(probe)) * probe * relative * width_scale
    controls = np.linspace(1.0, 10.0, 6400)
    return probe, reconstructed, controls


def test_electron_score_rejects_vertex_resolution_above_gate():
    probe, reconstructed, controls = _energy_fixture()
    truth_vertex = np.zeros((len(probe), 3))
    reconstructed_vertex = np.full((len(probe), 3), 0.20 / np.sqrt(3.0))

    thresholds = _thresholds()
    thresholds["vertex_resolution_gate_m"] = 0.10
    score = score_electron(
        probe, reconstructed, truth_vertex, reconstructed_vertex,
        controls, controls, thresholds,
    )

    assert score["gates"]["energy_resolution"] is True
    assert score["gates"]["vertex_resolution"] is False
    assert score["passed"] is False


def test_energy_resolution_target_is_not_the_gate():
    probe, reconstructed, controls = _energy_fixture(width_scale=2.2)
    truth_vertex = np.zeros((len(probe), 3))
    score = score_electron(
        probe, reconstructed, truth_vertex, np.zeros((len(probe), 3)),
        controls, controls, _thresholds(),
    )

    assert score["valid"] is True
    assert score["targets"]["R_1MeV"] > score["targets"]["R_1MeV_reference"]
    assert score["targets"]["R_1MeV"] <= score["gate_thresholds"]["energy_resolution_gate"]
    assert score["gates"]["energy_resolution"] is True
    assert score["passed"] is True

    probe, reconstructed, controls = _energy_fixture(width_scale=3.0)
    worse = score_electron(
        probe, reconstructed, truth_vertex, np.zeros((len(probe), 3)),
        controls, controls, _thresholds(),
    )
    assert worse["targets"]["R_1MeV"] > worse["gate_thresholds"]["energy_resolution_gate"]
    assert worse["gates"]["energy_resolution"] is False
    assert worse["passed"] is False


def test_electron_score_rejects_large_probe_energy_bias():
    probe, reconstructed, controls = _energy_fixture()
    biased = reconstructed * 1.05
    truth_vertex = np.zeros((len(probe), 3))
    reconstructed_vertex = np.zeros((len(probe), 3))

    score = score_electron(
        probe, biased, truth_vertex, reconstructed_vertex,
        controls, controls * 1.05, _thresholds(),
    )

    assert score["valid"] is True
    assert score["gates"]["energy_bias"] is False
    assert score["passed"] is False
    assert max(abs(item) for item in score["metrics"]["energy_bias_by_probe"]) > 0.02


def test_electron_score_rejects_radial_vertex_bias():
    probe, reconstructed, controls = _energy_fixture()
    truth_vertex = np.zeros((len(probe), 3))
    truth_vertex[:, 0] = 8.0
    reconstructed_vertex = truth_vertex.copy()
    reconstructed_vertex[:, 0] += 0.35

    score = score_electron(
        probe, reconstructed, truth_vertex, reconstructed_vertex,
        controls, controls, _thresholds(),
    )

    assert score["valid"] is True
    assert score["gates"]["vertex_bias"] is False
    assert score["passed"] is False
    assert max(abs(item) for item in score["metrics"]["vertex_radial_bias_by_probe_m"]) > 0.20


def test_missing_frozen_threshold_is_an_error():
    probe, reconstructed, controls = _energy_fixture(events_per_probe=200)
    broken = {key: value for key, value in _thresholds().items() if key != "energy_resolution_gate"}
    with pytest.raises(KeyError, match="energy_resolution_gate"):
        score_electron(
            probe, reconstructed, np.zeros((len(probe), 3)), np.zeros((len(probe), 3)),
            controls, controls, broken,
        )


def test_electron_prediction_requires_four_finite_scalars():
    assert parse_prediction((1.0, 0.0, 0.0, 0.0)) == (1.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="four finite"):
        parse_prediction((1.0, np.nan, 0.0))


def test_vertex_freeze_functions_round_upward_to_tenth_of_cm():
    assert freeze_threshold(0.08101) == 0.094
    assert freeze_threshold(0.08000) == 0.092
    assert freeze_gate(0.08101) == 0.122
    assert freeze_gate(0.08101, factor=1.5) == 0.122
    with pytest.raises(ValueError, match="above the reference"):
        freeze_gate(0.08101, factor=1.1)


def test_charge_pattern_oracle_has_a_finite_positive_vertex_limit():
    rms = charge_pattern_vertex_rms(
        np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]]),
        PMTLayout.uniform(128),
        DetectorConfig(),
    )

    assert np.isfinite(rms)
    assert rms > 0.0


def test_online_evaluator_splits_probe_and_control_roles():
    probe, reconstructed, controls = _energy_fixture()
    rng = np.random.default_rng(11)
    control_rec = controls + rng.normal(0.0, 0.01, len(controls))
    role = np.concatenate(
        (np.zeros(len(probe), dtype=np.int8), np.ones(len(controls), dtype=np.int8))
    )
    truth = {
        "evt_sample_role": role,
        "evt_e_true": np.concatenate((probe, controls)),
        "evt_e_vis": np.concatenate((probe, controls)),
        "evt_vertex_m": np.zeros((len(role), 3)),
    }
    prediction = np.column_stack(
        (
            np.concatenate((reconstructed, control_rec)),
            np.zeros((len(role), 3)),
        )
    )

    score = score_predictions(truth, prediction, _thresholds())

    assert score["valid"] is True


def test_online_evaluator_controls_validate_against_true_energy():
    probe, reconstructed, controls = _energy_fixture()
    role = np.concatenate(
        (np.zeros(len(probe), dtype=np.int8), np.ones(len(controls), dtype=np.int8))
    )
    truth = {
        "evt_sample_role": role,
        "evt_e_true": np.concatenate((probe, controls)),
        "evt_e_vis": np.concatenate((probe, 0.8 * controls)),
        "evt_vertex_m": np.zeros((len(role), 3)),
    }
    prediction = np.column_stack(
        (
            np.concatenate((reconstructed, controls)),
            np.zeros((len(role), 3)),
        )
    )

    score = score_predictions(truth, prediction, _thresholds())

    assert score["valid"] is True
