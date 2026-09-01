"""Tests for v2 population construction and public/private packaging."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import (
    SparseSplit,
    encode_event,
)
from benchmarks.JunoResBench.scripts.generate_v2_dataset import (
    CALIBRATION_ENERGIES_MEV,
    PROBE_KINETIC_MEV,
    combine_populations,
    make_population,
)
from benchmarks.JunoResBench.scripts.make_v2_benchmark import assemble_v2_package


def _observations(n):
    adc = np.full((1, 96), 16000, dtype=np.uint16)
    adc[0, 40:43] = [15990, 15970, 15990]
    return [
        encode_event(adc, np.array([i % 7]), 16000, 6, 4, 8)
        for i in range(n)
    ]


def _bundle(population, calibration=False):
    n = len(population["evt_e_true"])
    truth = dict(population)
    truth["evt_e_vis"] = population["evt_e_true"] + (
        0.0 if calibration else 1.022
    )
    truth["fiducial_radius_m"] = np.array([16.0])
    bundle = {
        "observations": _observations(n),
        "truth": truth,
        "metadata": {
            "seed": 123,
            "detector_config": {"secret": True},
            "energy_grid": [0.0, 0.5],
            "sample_interval_ns": 1.0,
        },
    }
    if calibration:
        bundle["labels"] = {
            "source_energy_mev": population["evt_e_true"],
            "deployment_position_m": population["evt_vertex_m"],
        }
    return bundle


def test_calibration_population_has_documented_energy_position_grid():
    population = make_population("calibration", seed=3, events_per_point=1)

    assert np.array_equal(
        np.unique(population["evt_e_true"]), CALIBRATION_ENERGIES_MEV
    )
    assert len(population["evt_e_true"]) == 5 * 13
    positions = np.unique(population["evt_vertex_m"], axis=0)
    assert len(positions) == 13
    assert set(np.unique(np.linalg.norm(positions, axis=1))) == {0.0, 8.0, 14.0}


def test_probe_grid_is_equal_count_positron_only():
    population = make_population("probes", seed=7, events_per_point=20)
    energies, counts = np.unique(population["evt_e_true"], return_counts=True)

    assert np.array_equal(energies, PROBE_KINETIC_MEV)
    assert np.array_equal(counts, np.full(10, 20))
    assert (population["evt_particle_type"] == 2).all()
    assert (population["evt_sample_role"] == 0).all()


def test_physics_vertices_are_uniform_in_volume():
    population = make_population(
        "controls", seed=11, events=10_000, fiducial_radius_m=16.0
    )
    r3 = np.linalg.norm(population["evt_vertex_m"], axis=1) ** 3

    assert abs(np.mean(r3) / 16.0**3 - 0.5) < 0.02
    assert (population["evt_particle_type"] == 2).all()
    assert (population["evt_sample_role"] == 1).all()


def test_default_controls_guarantee_scoring_bin_population():
    population = make_population("controls", seed=19)

    counts, _ = np.histogram(population["evt_e_true"], bins=64, range=(0.0, 11.0))

    assert len(population["evt_e_true"]) == 12_800
    assert (counts >= 200).all()


@pytest.fixture
def v2_package(tmp_path):
    calibration = make_population("calibration", seed=1, events_per_point=1)
    probes = make_population("probes", seed=2, events_per_point=2)
    controls = make_population("controls", seed=3, events=128)
    physics = combine_populations(probes, controls, seed=4)
    root = tmp_path / "package"
    assemble_v2_package(
        root,
        detector_geometry=np.arange(36, dtype=float).reshape(12, 3),
        calibration=_bundle(calibration, calibration=True),
        dev=_bundle(physics),
        final=_bundle(physics),
    )
    return root


def test_v2_package_is_positron_only_and_truth_clean(v2_package):
    observation_path = v2_package / "blind_truth_v2" / "final_observations"
    public = SparseSplit(observation_path)
    metadata = json.loads((observation_path / "metadata.json").read_text())

    assert not (observation_path / "truth.npz").exists()
    assert "evt_e_true" not in metadata
    assert "evt_e_vis" not in metadata
    assert "evt_vertex_m" not in metadata
    assert "seed" not in metadata
    assert "detector_config" not in metadata
    assert "energy_grid" not in metadata
    assert len(public) > 0

    with np.load(v2_package / "blind_truth_v2" / "truth.npz") as private:
        assert set(private["evt_sample_role"]) == {0, 1}
        assert (private["evt_particle_type"] == 2).all()


def test_probe_grid_and_package_boundaries(v2_package):
    with np.load(v2_package / "blind_truth_v2" / "truth.npz") as truth:
        probe = truth["evt_sample_role"] == 0
        assert np.array_equal(
            np.unique(truth["evt_e_true"][probe]), PROBE_KINETIC_MEV
        )
        transitions = np.count_nonzero(np.diff(truth["evt_sample_role"]) != 0)
        assert transitions > 2

    with np.load(v2_package / "task_v2" / "calibration" / "labels.npz") as labels:
        assert set(labels.files) == {"source_energy_mev", "deployment_position_m"}
    assert (v2_package / "task_v2" / "dev" / "truth.npz").exists()
    assert (v2_package / "task_v2" / "detector_geometry.npz").exists()
    assert (v2_package / "task_v2" / "evaluate.py").exists()
    assert (v2_package / "task_v2" / "TASK.md").exists()
