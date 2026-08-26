"""Stage 1 unit tests: Birks quenching, nonlinearity curve, dispatch guard.

Run: python3 benchmarks/JunoResBench/tests/test_stage1.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.stages.s1_response import (
    nl_corr,
    run_s1,
)
from benchmarks.JunoResBench.juno_res_bench.truth import (
    EventInput,
    ParticleType,
)


def test_birks_default_on():
    cfg = DetectorConfig()
    s1 = run_s1(EventInput(0, 0, 0, 1.0), cfg)
    expect = 1.0 / 1.0241 * (1 - cfg.nl_amp * np.exp(-1.0))
    assert abs(s1.e_vis_mev / 1.0 - expect) < 1e-9
    assert abs(s1.e_dep_mev - 1.0) < 1e-12
    print(f"ok  birks+nl ON: E_vis/E_true = {s1.e_vis_mev:.4f} (expect {expect:.4f})")


def test_nl_curve():
    cfg = DetectorConfig()
    e = np.geomspace(0.1, 20.0, 200)
    nl = np.array([nl_corr(x, cfg) for x in e])
    # monotonic increase toward 1, continuous
    assert (np.diff(nl) > 0).all()
    assert abs(nl[-1] - 1.0) < 1e-3
    assert nl[0] < 1.0
    # anchor: ~-0.7% at 1 MeV
    assert abs(nl_corr(1.0, cfg) - (1 - cfg.nl_amp * np.exp(-1.0))) < 1e-9
    print(f"ok  nl curve: nl(0.1)={nl[0]:.4f}, nl(1)={nl_corr(1.0, cfg):.4f}, "
          f"nl(10)={nl_corr(10.0, cfg):.4f}")


def test_dispatch_guard():
    cfg = DetectorConfig()
    for pt in (ParticleType.GAMMA, ParticleType.POSITRON):
        try:
            run_s1(EventInput(0, 0, 0, 1.0, particle_type=pt), cfg)
            raise AssertionError(f"{pt} should be v1")
        except NotImplementedError:
            pass
    print("ok  gamma/positron dispatch guard")


def test_end_to_end_linearity():
    """pe/E should now show mild nonlinearity (rising toward high E)."""
    cfg = DetectorConfig()
    sim = DetectorSim(cfg, PMTLayout.uniform(), seed=11)
    rng = np.random.default_rng(3)
    out = {}
    for e_true in (0.5, 1.0, 5.0):
        pe = [
            sim.generate(
                *(np.array([0.0, 0.0, 0.0]) + 2.0 * rng.uniform(-1, 1, 3)),
                e_true, with_waveforms=False,
            ).n_pe_total
            / e_true
            for _ in range(300)
        ]
        out[e_true] = float(np.mean(pe))
    # quench+nl make low-E yield lower pe/MeV than high-E
    assert out[0.5] < out[1.0] < out[5.0], f"nonlinearity direction wrong: {out}"
    print(f"ok  pe/MeV nonlinearity: 0.5MeV={out[0.5]:.1f} < "
          f"1MeV={out[1.0]:.1f} < 5MeV={out[5.0]:.1f}")


if __name__ == "__main__":
    test_birks_default_on()
    test_nl_curve()
    test_dispatch_guard()
    test_end_to_end_linearity()
    print("\nall stage-1 tests passed")
