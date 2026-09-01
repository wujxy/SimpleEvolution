"""Tests for the v2 JUNO-style energy-resolution score."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.resolution import (
    fit_peak,
    fit_resolution_curve,
    score_v2,
    validate_response,
)
from benchmarks.JunoResBench.scripts.evaluate_v2 import load_inputs


def test_peak_fit_rejects_sparse_and_ignores_far_outliers():
    core = np.linspace(0.9, 1.1, 200)
    values = np.concatenate((core, [10.0, 20.0]))

    mean, sigma = fit_peak(values)

    assert np.isclose(mean, 1.0, atol=1e-12)
    assert sigma < 0.07
    with pytest.raises(ValueError, match="at least 100"):
        fit_peak(np.ones(99))


def test_curve_fit_recovers_full_one_mev_resolution():
    energy = np.array([1.02, 1.5, 2, 3, 4, 5, 6, 8, 10, 12])
    a, b, c = 0.026, 0.006, 0.012
    relative = np.sqrt(a * a / energy + b * b + c * c / energy**2)

    fit = fit_resolution_curve(energy, relative * energy)

    assert np.isclose(fit.a, a, rtol=2e-3)
    assert np.isclose(fit.b, b, rtol=2e-3)
    assert np.isclose(fit.c, c, rtol=2e-3)
    assert np.isclose(
        fit.r_1mev, np.sqrt(a * a + b * b + c * c), rtol=2e-3
    )


def test_invalid_outputs_are_rejected():
    truth = np.linspace(1.022, 12.022, 6400)

    assert validate_response(truth, np.full_like(truth, 3.0))
    assert validate_response(truth, np.round(truth * 2) / 2)
    assert validate_response(truth, truth) == []


def test_malformed_control_outputs_are_rejected():
    truth = np.linspace(1.022, 12.022, 6400)

    assert validate_response(truth, truth[:-1])
    broken = truth.copy()
    broken[7] = np.nan
    assert validate_response(truth, broken)
    assert validate_response(truth[:6399], truth[:6399])


def test_score_has_one_three_percent_success_condition():
    kinetic = np.array([0, 0.5, 1, 2, 3, 4, 5, 6, 8, 11.0])
    visible = kinetic + 1.022
    a, b, c = 0.022, 0.004, 0.008
    z = np.random.default_rng(8).normal(size=4000)
    probe_kinetic = np.repeat(kinetic, len(z))
    probe_rec = np.concatenate([
        energy + z * energy * np.sqrt(
            a * a / energy + b * b + c * c / energy**2
        )
        for energy in visible
    ])
    control_true = np.linspace(1.022, 12.022, 6400)

    score = score_v2(
        probe_kinetic,
        probe_rec,
        control_true,
        control_true.copy(),
    )

    assert score["valid"] is True
    assert score["passed"] is True
    assert score["target"] == 0.03
    assert score["R_1MeV"] <= score["target"]
    assert len(score["points"]) == 10


def test_invalid_response_does_not_reach_curve_fit():
    score = score_v2(
        np.zeros(100),
        np.ones(100),
        np.linspace(1.022, 12.022, 6400),
        np.full(6400, 3.0),
    )

    assert score["valid"] is False
    assert score["passed"] is False
    assert "R_1MeV" not in score


def test_energy_only_prediction_contract_rejects_extra_keys(tmp_path):
    truth_path = tmp_path / "truth.npz"
    pred_path = tmp_path / "prediction.npz"
    np.savez(
        truth_path,
        evt_sample_role=np.array([0, 1]),
        evt_e_true=np.array([0.0, 1.0]),
        evt_e_vis=np.array([1.0, 2.0]),
    )
    np.savez(pred_path, E_rec=np.array([1.0, 2.0]), x_rec=np.zeros(2))

    with pytest.raises(ValueError, match="exactly one array named E_rec"):
        load_inputs(truth_path, pred_path)
