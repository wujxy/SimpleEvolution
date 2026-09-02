"""Tests for local charged-particle stopping power and Birks response."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stopping_power import (
    birks_visible_mev,
    charged_steps,
    electron_stopping_power_mev_cm,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig


def test_low_energy_stopping_power_rises():
    energy = np.array([0.02, 0.10, 1.0, 5.0])
    dedx = electron_stopping_power_mev_cm(energy)

    assert dedx[0] > dedx[1] > dedx[2]
    assert (dedx > 0).all()


def test_local_birks_response_suppresses_dense_low_energy_steps():
    deposited = np.full(3, 0.01)
    dedx = np.array([2.0, 5.0, 20.0])

    fraction = birks_visible_mev(deposited, dedx, 0.012) / deposited

    assert fraction[0] > fraction[1] > fraction[2]
    assert np.all((fraction > 0) & (fraction < 1))


def test_charged_steps_conserve_energy_and_resolve_track():
    deposited, kinetic_midpoint, length_cm = charged_steps(
        1.0, step_fraction=0.05, cut_mev=0.002
    )

    assert len(deposited) > 10
    assert np.isclose(deposited.sum(), 1.0, atol=1e-12)
    assert (deposited > 0).all()
    assert (length_cm > 0).all()
    assert np.all(np.diff(kinetic_midpoint) < 0)


def test_zero_energy_has_no_steps():
    deposited, kinetic_midpoint, length_cm = charged_steps(0.0)

    assert deposited.size == kinetic_midpoint.size == length_cm.size == 0


def test_detector_config_exposes_only_local_response_controls_for_v2():
    cfg = DetectorConfig()

    assert cfg.birks_kb_cm_per_mev == 0.012
    assert cfg.charged_step_fraction == 0.05
    assert cfg.charged_transport_cut_mev == 0.002
