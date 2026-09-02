"""Trace-mode tests: per-photon transport vs fast-mode folded physics.

Run: python3 benchmarks/JunoResBench/tests/test_trace.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.optics_tables import (
    sample_emission_lambda,
)


def test_yield_consistency():
    """Trace and fast modes agree on the calibrated center yield."""
    lay = PMTLayout.uniform()
    fast = np.mean([DetectorSim(DetectorConfig(), lay, seed=s)
                    .generate(0, 0, 0, 1.0, with_waveforms=False).n_pe_total
                    for s in range(30)])
    sim = DetectorSim(DetectorConfig(optics_mode="trace"), lay, seed=1)
    tr = np.mean([sim.generate(0, 0, 0, 1.0, with_waveforms=False).n_pe_total
                  for _ in range(30)])
    assert abs(tr - fast) / fast < 0.05, f"trace {tr:.0f} vs fast {fast:.0f}"
    print(f"ok  yield: trace {tr:.0f} vs fast {fast:.0f} pe "
          f"({(tr/fast-1)*100:+.1f}%)")


def test_red_shift():
    """Arrived photons are red-shifted: UV photons absorbed + re-emitted."""
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import (
        EventInput,
        ParticleType,
    )
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages import (
        s1_response,
        s2_photons,
        s3_trace,
    )
    cfg = DetectorConfig(optics_mode="trace")
    ev_in = EventInput(0, 0, 0, 2.0)
    s1 = s1_response.run_s1(ev_in, cfg)
    ph = s2_photons.run_s2_scint(s1, ev_in, cfg, np.random.default_rng(3))
    s3 = s3_trace.trace_photons(ph, ev_in, cfg, PMTLayout.uniform(),
                                np.random.default_rng(4),
                                grid=None)
    lam_em = sample_emission_lambda(np.random.default_rng(5), len(s3.lam_nm))
    assert np.mean(s3.lam_nm) > np.mean(lam_em) + 2.0, (
        f"no red shift: arrived {np.mean(s3.lam_nm):.1f} vs emitted "
        f"{np.mean(lam_em):.1f}"
    )
    print(f"ok  red shift: arrived <lambda>={np.mean(s3.lam_nm):.1f} nm > "
          f"emitted {np.mean(lam_em):.1f} nm")


def test_timing_tail():
    """Trace timing has re-emission/scatter tails the fast mode folds away."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(DetectorConfig(optics_mode="trace"), lay, seed=3)
    tof_all = []
    for _ in range(20):
        ev = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
        tof_all.append(ev.t_tof_ns)   # center: pure flight + re-emission
    tof = np.concatenate(tof_all)
    # straight-line flight would be 96.2 ns; re-emission (1.5 ns) + scattering
    # path randomization create a tail above it
    assert np.mean(tof) > 96.5, f"no propagation tail: mean {np.mean(tof):.2f}"
    assert np.quantile(tof, 0.99) > 100.0
    print(f"ok  timing tail: mean tof {np.mean(tof):.2f} ns "
          f"(straight = 96.2), 99% q = {np.quantile(tof, 0.99):.1f}")


def test_determinism():
    a = DetectorSim(DetectorConfig(optics_mode="trace"), PMTLayout.uniform(),
                    seed=7).generate(1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    b = DetectorSim(DetectorConfig(optics_mode="trace"), PMTLayout.uniform(),
                    seed=7).generate(1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    assert a.n_pe_total == b.n_pe_total
    assert np.array_equal(a.t_rel_ns, b.t_rel_ns)
    print("ok  trace determinism")


def test_cherenkov_cone_preserved():
    """Cherenkov photons keep their cone through the trace transport."""
    cfg = DetectorConfig(optics_mode="trace")
    lay = PMTLayout.uniform()
    sim = DetectorSim(cfg, lay, seed=8)
    track = np.array([1.0, -1.0, 2.0]) / np.sqrt(6.0)
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s2_photons import (
        beta_from_kinetic,
    )
    theta_c = np.degrees(np.arccos(1.0 / (cfg.ls_refractive_index
                                          * beta_from_kinetic(3.0))))
    med = []
    for _ in range(20):
        ev = sim.generate(0, 0, 0, 3.0, with_waveforms=False,
                          direction=tuple(track))
        pe_pmt = np.repeat(ev.pmt_ids, np.diff(ev.pe_offsets))
        pd = lay.positions_m[pe_pmt] / np.linalg.norm(
            lay.positions_m[pe_pmt], axis=1, keepdims=True)
        m = ev.pe_type == 1
        med.append(np.median(np.degrees(np.arccos(
            np.clip(pd[m] @ track, -1, 1)))))
    assert abs(np.median(med) - theta_c) < 10.0
    print(f"ok  Cherenkov cone in trace mode: median {np.median(med):.1f} deg "
          f"vs theta_C {theta_c:.1f}")


if __name__ == "__main__":
    test_yield_consistency()
    test_red_shift()
    test_timing_tail()
    test_determinism()
    test_cherenkov_cone_preserved()
    print("\nall trace-mode tests passed")
