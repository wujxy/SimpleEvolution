"""Stage 2 unit tests: scintillation + Cherenkov photon generation.

Run: python3 benchmarks/JunoResBench/tests/test_stage2.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s1_response import run_s1
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s2_photons import (
    beta_from_kinetic,
    run_s2_cherenkov,
    run_s2_scint,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import EventInput


def test_photon_wavelength_contract_survives_concatenation():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import PhotonSoA

    a = PhotonSoA(
        photon_type=np.array([0], np.int8),
        pos_m=np.zeros((1, 3), np.float32),
        dir=np.array([[0, 0, 1]], np.float32),
        t_emit_ns=np.zeros(1, np.float32),
        step_idx=np.zeros(1, np.int32),
        wavelength_nm=np.array([430.0]),
    )
    b = PhotonSoA(
        photon_type=np.array([1], np.int8),
        pos_m=np.zeros((1, 3), np.float32),
        dir=np.array([[0, 1, 0]], np.float32),
        t_emit_ns=np.zeros(1, np.float32),
        step_idx=np.zeros(1, np.int32),
        wavelength_nm=np.array([350.0]),
    )
    out = PhotonSoA.concatenate([a, b])
    assert np.array_equal(out.wavelength_nm, [430.0, 350.0])
    assert PhotonSoA.empty().wavelength_nm.shape == (0,)


def test_scintillation_and_cherenkov_have_distinct_spectra():
    cfg = DetectorConfig(ly_photons_mev=2000.0)
    ev = EventInput(0, 0, 0, 3.0)
    s1 = run_s1(ev, cfg)
    scint = run_s2_scint(s1, ev, cfg, np.random.default_rng(41))
    cher = run_s2_cherenkov(s1, ev, cfg, np.random.default_rng(42))
    assert np.isfinite(scint.wavelength_nm).all()
    assert np.isfinite(cher.wavelength_nm).all()
    scint_uv = np.mean(scint.wavelength_nm < 340.0)
    cher_uv = np.mean(cher.wavelength_nm < 340.0)
    assert cher_uv > scint_uv + 0.10


def test_cherenkov_cone_uses_each_photon_wavelength():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import (
        ls_refractive_index,
    )

    photons, _, s1 = _cherenkov(3.0)
    step_dir = s1.steps.dir[photons.step_idx]
    beta = np.array([beta_from_kinetic(e) for e in s1.steps.kinetic_mev])
    expected = 1.0 / (
        beta[photons.step_idx] * ls_refractive_index(photons.wavelength_nm)
    )
    observed = np.einsum("ij,ij->i", photons.dir.astype(float), step_dir)
    assert np.allclose(observed, expected, atol=2e-3)


def test_ls_group_index_is_dispersive():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import (
        ls_group_index,
    )

    ng = ls_group_index(np.array([350.0, 430.0, 500.0]))
    assert np.all(np.diff(ng) < 0.0)
    assert np.all((ng > 1.45) & (ng < 1.75))


def test_beta():
    assert abs(beta_from_kinetic(1.0) - 0.9411) < 1e-3
    assert beta_from_kinetic(0.01) < 1 / 1.49   # below threshold
    print(f"ok  beta(1 MeV)={beta_from_kinetic(1.0):.4f}, "
          f"beta(0.01)={beta_from_kinetic(0.01):.3f} (< 1/n)")


def _cherenkov(e_mev: float, direction=(0.0, 0.0, 1.0)):
    cfg = DetectorConfig()
    ev = EventInput(0, 0, 0, e_mev, direction=direction)
    s1 = run_s1(ev, cfg)
    return run_s2_cherenkov(s1, ev, cfg, np.random.default_rng(7)), cfg, s1


def test_below_threshold():
    p, _, _ = _cherenkov(0.05)
    assert len(p) == 0
    print("ok  below Cherenkov threshold -> no photons")


def test_yield_fraction():
    cfg = DetectorConfig()
    ev = EventInput(0, 0, 0, 1.0)
    s1 = run_s1(ev, cfg)
    rng = np.random.default_rng(1)
    n_c = [len(run_s2_cherenkov(s1, ev, cfg, rng)) for _ in range(200)]
    n_s = s1.e_vis_mev * cfg.ly_photons_mev
    frac = np.mean(n_c) / n_s
    assert 0.02 < frac < 0.03, f"Cherenkov fraction {frac:.4f} out of range"
    print(f"ok  N_C/N_scint @1 MeV = {frac:.4f} (target ~0.025)")


def test_scintillation_count_is_poisson_at_low_mean():
    cfg = DetectorConfig(ly_photons_mev=1.0)
    ev = EventInput(0, 0, 0, 1.0)
    s1 = run_s1(ev, cfg)
    rng = np.random.default_rng(13)

    counts = np.array([len(run_s2_scint(s1, ev, cfg, rng)) for _ in range(2000)])
    expected = s1.e_vis_mev * cfg.ly_photons_mev

    assert abs(counts.mean() - expected) < 0.08
    assert abs(np.mean(counts == 0) - np.exp(-expected)) < 0.08


def test_cone_geometry():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import (
        ls_refractive_index,
    )

    p, cfg, s1 = _cherenkov(3.0, direction=(1.0, -1.0, 2.0))
    assert len(p) > 100
    step_dir = s1.steps.dir[p.step_idx]
    cos_tc = np.sum(p.dir.astype(np.float64) * step_dir, axis=1)
    beta = np.array([beta_from_kinetic(e) for e in s1.steps.kinetic_mev])
    expect = 1.0 / (
        ls_refractive_index(p.wavelength_nm) * beta[p.step_idx]
    )
    assert np.allclose(cos_tc, expect, atol=2e-3)
    assert np.allclose(p.t_emit_ns, s1.steps.t_ns[p.step_idx])
    assert (p.photon_type == 1).all()
    print("ok  per-step Cherenkov cone and prompt step time")


def test_stream_isolation_end_to_end():
    """Enabling Cherenkov must not change the scintillation chain draws."""
    lay = PMTLayout.uniform()
    cfg_on = DetectorConfig()
    cfg_off = DetectorConfig(cherenkov_photons_per_m=None)
    e_on = DetectorSim(cfg_on, lay, seed=5).generate(1.0, 2.0, -1.0, 3.0,
                                                     with_waveforms=False)
    e_off = DetectorSim(cfg_off, lay, seed=5).generate(1.0, 2.0, -1.0, 3.0,
                                                       with_waveforms=False)
    assert e_on.n_gamma_cher > 0
    assert e_off.n_gamma_cher == 0
    assert e_on.n_gamma == e_off.n_gamma
    # compare the scintillation subsets (Cherenkov PEs extra in e_on)
    assert np.array_equal(
        np.sort(e_on.t_rel_ns[e_on.pe_type == 0]), np.sort(e_off.t_rel_ns)
    )
    assert np.array_equal(
        np.sort(e_on.q_pe[e_on.pe_type == 0]), np.sort(e_off.q_pe)
    )
    print(f"ok  stream isolation: cher ON/OFF identical scint chain "
          f"(n_gamma_cher={e_on.n_gamma_cher})")


if __name__ == "__main__":
    test_beta()
    test_below_threshold()
    test_yield_fraction()
    test_cone_geometry()
    test_stream_isolation_end_to_end()
    print("\nall stage-2 tests passed")
