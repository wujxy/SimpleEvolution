"""Release-gate tests for the JunoResBench v2 world validator."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.resolution import PROBE_KINETIC_MEV
from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import encode_event
from benchmarks.JunoResBench.scripts.validate_v2_world import (
    _observation_hash,
    validate_world,
)


def _synthetic_truth(events_per_energy, controls=12800, seed=5):
    """A physics-consistent private truth without running the generator."""
    rng = np.random.default_rng(seed)
    energies = np.repeat(PROBE_KINETIC_MEV, events_per_energy)
    strata = np.arange(controls) % 64
    continuous = 11.0 * (strata + rng.random(controls)) / 64.0
    kinetic = np.concatenate((energies, continuous))
    order = rng.permutation(len(kinetic))

    radius = 16.0 * rng.random(len(kinetic)) ** (1.0 / 3.0)
    cos_theta = rng.uniform(-1.0, 1.0, len(kinetic))
    sin_theta = np.sqrt(1.0 - cos_theta**2)
    phi = rng.uniform(0.0, 2.0 * np.pi, len(kinetic))
    vertex = np.column_stack((
        radius * sin_theta * np.cos(phi),
        radius * sin_theta * np.sin(phi),
        radius * cos_theta,
    ))

    offsets = [0]
    step_pos = []
    step_dep = []
    step_vis = []
    step_dedx = []
    step_kinetic = []
    step_length = []
    step_kind = []
    e_vis = []
    for index in order:
        energy = kinetic[index]
        visible = 0.0
        primary = [
            (min(energy, 1.0), 1.0, 0.98),
            (max(energy - 1.0, 0.0), 0.03, 0.90),
        ]
        for dep, kinetic_mid, fraction in primary:
            if dep <= 0.0:
                continue
            step_pos.append(vertex[index])
            step_dep.append(dep)
            step_vis.append(fraction * dep)
            step_dedx.append(1.5)
            step_kinetic.append(kinetic_mid)
            step_length.append(dep / 150.0)
            step_kind.append(0)
            visible += fraction * dep
        for offset in ((0.06, 0.0, 0.0), (-0.05, 0.01, 0.0)):
            step_pos.append(vertex[index] + np.asarray(offset))
            step_dep.append(0.510999)
            step_vis.append(0.98 * 0.510999)
            step_dedx.append(1.6)
            step_kinetic.append(0.5)
            step_length.append(0.510999 / 160.0)
            step_kind.append(3)
            visible += 0.98 * 0.510999
        offsets.append(len(step_dep))
        e_vis.append(visible)

    return {
        "evt_e_true": kinetic[order],
        "evt_e_vis": np.asarray(e_vis),
        "evt_vertex_m": vertex[order],
        "evt_particle_type": np.full(len(kinetic), 2, dtype=np.int8),
        "evt_sample_role": np.concatenate((
            np.zeros(len(energies), dtype=np.int8),
            np.ones(controls, dtype=np.int8),
        ))[order],
        "evt_e_dep_mev": kinetic[order] + 1.021998,
        "evt_e_escape_mev": np.zeros(len(kinetic)),
        "evt_total_energy": kinetic[order] + 1.021998,
        "step_offsets": np.asarray(offsets, dtype=np.int64),
        "step_pos_m": np.asarray(step_pos),
        "step_e_dep_mev": np.asarray(step_dep),
        "step_e_vis_mev": np.asarray(step_vis),
        "step_dedx_mev_cm": np.asarray(step_dedx),
        "step_kinetic_mev": np.asarray(step_kinetic),
        "step_length_m": np.asarray(step_length),
        "step_kind": np.asarray(step_kind, dtype=np.int8),
        "fiducial_radius_m": np.asarray([16.0]),
    }


def _predictions(truth, a, b, c, seed=9):
    """E_rec around truth with a genuine positive resolution profile."""
    rng = np.random.default_rng(seed)
    energy = truth["evt_e_vis"]
    sigma = energy * np.sqrt(a * a / energy + b * b + c * c / energy**2)
    return energy + sigma * rng.standard_normal(len(energy))


def test_release_gate_rejects_unreachable_constant_reference():
    truth = _synthetic_truth(events_per_energy=150)
    constant = np.full(len(truth["evt_e_true"]), 3.0)
    baseline = _predictions(truth, 0.07, 0.02, 0.03)

    report = validate_world(truth, baseline, constant, bootstrap_replicates=50)

    assert not report["release_ready"]
    assert "reference_does_not_reach_target" in report["failures"]


def test_release_gate_rejects_unstable_and_unreachable_fixture():
    truth = _synthetic_truth(events_per_energy=2000)
    baseline = _predictions(truth, 0.07, 0.02, 0.03)
    reference = _predictions(truth, 0.030, 0.012, 0.014, seed=11)

    report = validate_world(truth, baseline, reference, bootstrap_replicates=50)

    assert "score_boundary_unstable" in report["failures"]
    assert "reference_does_not_reach_target" in report["failures"]
    assert not report["release_ready"]


def test_release_gate_rejects_an_invalid_public_baseline():
    truth = _synthetic_truth(events_per_energy=2000)
    invalid_baseline = np.full(len(truth["evt_e_true"]), 3.0)
    reference = _predictions(truth, 0.019, 0.006, 0.009)

    report = validate_world(
        truth, invalid_baseline, reference, bootstrap_replicates=20
    )

    assert "public_baseline_invalid" in report["failures"]
    assert not report["release_ready"]


def test_bootstrap_boundary_is_stable():
    truth = _synthetic_truth(events_per_energy=6000)
    baseline = _predictions(truth, 0.07, 0.02, 0.03)
    reference = _predictions(truth, 0.019, 0.006, 0.009)

    report = validate_world(truth, baseline, reference, bootstrap_replicates=200)

    assert report["score_bootstrap_std_percent_point"] <= 0.03
    assert report["reference_R_1MeV_percent"] <= 3.0
    assert report["baseline_R_1MeV_percent"] > 3.0
    assert report["release_ready"], report["failures"]


def test_reproducibility_hash_includes_waveform_samples():
    adc = np.full((1, 64), 16000, dtype=np.uint16)
    adc[0, 20] = 15980
    first = encode_event(adc, np.array([3]), 16000, 6, 4, 8)
    adc[0, 20] = 15979
    second = encode_event(adc, np.array([3]), 16000, 6, 4, 8)

    assert _observation_hash([first]) != _observation_hash([second])
