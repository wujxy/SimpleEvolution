"""Stage 3 unit tests: scatter timing, Cherenkov ray transport, consistency.

Run: python3 benchmarks/JunoResBench/tests/test_stage3.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import (
    coverage_fraction,
    PMTLayout,
)
from benchmarks.JunoResBench.juno_res_bench.stages.s2_photons import (
    beta_from_kinetic,
)


def test_cherenkov_arrival_fraction():
    """Arrived Cherenkov photons / emitted ~ geometric coverage."""
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()
    sim = DetectorSim(cfg, lay, seed=3)
    emitted = arrived = 0
    for _ in range(20):
        ev = sim.generate(0, 0, 0, 3.0, with_waveforms=False)
        emitted += ev.n_gamma_cher
        arrived += ev.n_arrived - ev.n_gamma
    frac = arrived / emitted
    cov = coverage_fraction(lay, cfg.pmt_diameter_m)
    assert abs(frac - cov) < 0.03, f"arrival fraction {frac:.3f} vs coverage {cov:.3f}"
    print(f"ok  Cherenkov arrival fraction {frac:.3f} ~ coverage {cov:.3f}")


def test_detection_rate_consistency():
    """det_scale compensation: Cherenkov and scint thinning rates agree."""
    cfg = DetectorConfig()
    sim = DetectorSim(cfg, PMTLayout.uniform(), seed=4)
    c_pe = c_em = s_pe = s_em = 0
    for _ in range(20):
        ev = sim.generate(0, 0, 0, 3.0, with_waveforms=False)
        c_pe += int((ev.pe_type == 1).sum()); c_em += ev.n_gamma_cher
        s_pe += int((ev.pe_type == 0).sum()); s_em += ev.n_gamma
    assert abs(c_pe / c_em - s_pe / s_em) < 0.01
    print(f"ok  detection rates equal: cher {c_pe/c_em:.4f} vs scint {s_pe/s_em:.4f}")


def test_cone_angular_structure():
    """Arrived Cherenkov photons sit near theta_C from the track axis."""
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()
    sim = DetectorSim(cfg, lay, seed=5)
    track = np.array([1.0, -1.0, 2.0]) / np.sqrt(6.0)
    theta_c = np.degrees(np.arccos(1.0 / (cfg.ls_refractive_index
                                           * beta_from_kinetic(3.0))))
    angles = []
    for _ in range(30):
        ev = sim.generate(0, 0, 0, 3.0, with_waveforms=False,
                          direction=tuple(track))
        # per-PE PMT id via the ragged structure
        pe_pmt = np.repeat(ev.pmt_ids, np.diff(ev.pe_offsets))
        pmt_dirs = lay.positions_m[pe_pmt] / np.linalg.norm(
            lay.positions_m[pe_pmt], axis=1, keepdims=True)
        m = ev.pe_type == 1
        ang = np.degrees(np.arccos(np.clip(pmt_dirs[m] @ track, -1, 1)))
        angles.append(ang)
    ang = np.concatenate(angles)
    med = np.median(ang)
    # isotropic baseline would have median ~90 deg
    assert abs(med - theta_c) < 10.0, f"median {med:.1f} vs theta_C {theta_c:.1f}"
    frac_near = ((ang > theta_c - 15) & (ang < theta_c + 15)).mean()
    assert frac_near > 0.5
    print(f"ok  cone structure: theta_C={theta_c:.1f} deg, median={med:.1f}, "
          f"frac within +-15 deg = {frac_near:.2f}")


def test_scatter_timing():
    """Residual = TTS (+ scatter + offset); scatter grows with path length."""
    cfg = DetectorConfig()
    sim = DetectorSim(cfg, PMTLayout.uniform(), seed=6)
    ev = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
    res = ev.t_rel_ns - ev.t_emit_ns - ev.t_tof_ns - cfg.pre_trigger_ns
    expect = np.sqrt(cfg.tts_sigma_ns**2
                     + (cfg.a_scatter_ns_per_m * cfg.detector_radius_m)**2
                     + cfg.time_offset_sigma_ns**2)
    assert abs(res.std() - expect) < 0.25, f"{res.std():.3f} vs {expect:.3f}"
    assert res.std() > cfg.tts_sigma_ns
    print(f"ok  scatter timing: residual {res.std():.2f} ns "
          f"(TTS {cfg.tts_sigma_ns} + scatter + offset, expect ~{expect:.2f})")


def test_stream_isolation_transport():
    """Cherenkov transport must not change the scintillation chain.

    With Cherenkov ON the PE arrays additionally contain Cherenkov PEs, so
    we compare the scintillation subsets (as sorted multisets).
    """
    lay = PMTLayout.uniform()
    e_on = DetectorSim(DetectorConfig(), lay, seed=8).generate(
        1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    e_off = DetectorSim(DetectorConfig(ly_cherenkov=None), lay, seed=8).generate(
        1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    assert e_on.n_gamma == e_off.n_gamma
    assert np.array_equal(
        np.sort(e_on.t_emit_ns[e_on.pe_type == 0]), np.sort(e_off.t_emit_ns)
    )
    s_on = np.sort(e_on.t_rel_ns[e_on.pe_type == 0])
    s_off = np.sort(e_off.t_rel_ns)
    assert np.array_equal(s_on, s_off)
    assert np.array_equal(np.sort(e_on.q_pe[e_on.pe_type == 0]),
                          np.sort(e_off.q_pe))
    print("ok  transport stream isolation (scint subset bit-identical)")


if __name__ == "__main__":
    test_cherenkov_arrival_fraction()
    test_detection_rate_consistency()
    test_cone_angular_structure()
    test_scatter_timing()
    test_stream_isolation_transport()
    print("\nall stage-3 tests passed")
