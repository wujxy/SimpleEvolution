"""Stage 4 unit tests: CE(theta) angular efficiency + per-PMT PDE spread.

Run: python3 benchmarks/JunoResBench/tests/test_stage4.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s4_detection import ce_factor


def test_ce_interpolation():
    cfg = DetectorConfig()
    # table anchors reproduced exactly
    assert ce_factor(cfg, np.array([1.0]))[0] == cfg.ce_eff[0]
    assert abs(ce_factor(cfg, np.array([0.0]))[0] - cfg.ce_eff[-1]) < 1e-12
    # monotone-ish decrease at large angles, ~1 at small angles
    cos = np.cos(np.radians([0, 30, 55, 85, 90]))
    ce = ce_factor(cfg, cos)
    assert ce[0] == 1.0 and ce[-1] < 0.8
    assert np.all(np.diff(ce[[0, 2, 3, 4]]) <= 1e-9)
    print(f"ok  CE table: CE(0)={ce[0]:.3f}, CE(55)={ce[2]:.3f}, CE(90)={ce[-1]:.3f}")


def test_yield_anchored():
    """Event-wise normalization keeps total pe yield at the calibration."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(DetectorConfig(), lay, seed=21)
    pe = [sim.generate(0, 0, 0, 1.0, with_waveforms=False).n_pe_total
          for _ in range(300)]
    m = np.mean(pe)
    # quench 0.9765 x nl 0.9926 x 1500 = 1453; stat tol ~3 sigma
    assert abs(m - 1453) < 45, f"mean pe {m:.0f} drifted from anchor 1453"
    print(f"ok  yield anchored: {m:.0f} pe @1 MeV (anchor ~1453)")


def test_pde_delta_recoverable():
    """Per-PMT PDE offsets can be calibrated back from detected counts."""
    cfg = DetectorConfig(pde_sigma=0.30)   # exaggerate for test power
    lay = PMTLayout.uniform(400, 19.365)
    sim = DetectorSim(cfg, lay, seed=22)
    rng = np.random.default_rng(9)
    counts = np.zeros(lay.n_pmt)
    n_ev = 3000
    for _ in range(n_ev):
        u = 16.0 * rng.random() ** (1 / 3)
        ct = rng.uniform(-1, 1); st = np.sqrt(1 - ct * ct)
        phi = rng.uniform(0, 2 * np.pi)
        ev = sim.generate(*(u * st * np.cos(phi), u * st * np.sin(phi), u * ct),
                          4.0, with_waveforms=False)
        counts[ev.pmt_ids] += ev.n_pe_pmt
    expected = np.full(lay.n_pmt, counts.mean())
    # correlation between observed excess and true pde_delta
    excess = (counts - expected) / np.sqrt(np.maximum(counts, 1))
    corr = np.corrcoef(excess, sim.calib.pde_delta)[0, 1]
    assert corr > 0.6, f"calibration correlation only {corr:.2f}"
    print(f"ok  pde_delta recoverable: corr={corr:.2f} over {lay.n_pmt} PMTs")


def test_angular_suppression_off_center():
    """Off-center vertices see larger incidence angles -> lower CE."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(DetectorConfig(), lay, seed=23)
    center = np.mean([sim.generate(0, 0, 0, 1.0, with_waveforms=False).n_pe_total
                      for _ in range(400)])
    off = np.mean([sim.generate(15.0, 0, 0, 1.0, with_waveforms=False).n_pe_total
                   for _ in range(400)])
    # mu_pe(15m)=0.944; mean CE at r=15 ~0.93 -> total ~0.87
    ratio = off / center
    assert 0.84 < ratio < 0.90, f"off/center ratio {ratio:.3f} unexpected"
    print(f"ok  angular suppression: off/center pe ratio = {ratio:.3f} "
          f"(mu_pe 0.944 x mean CE ~0.93)")


if __name__ == "__main__":
    test_ce_interpolation()
    test_yield_anchored()
    test_pde_delta_recoverable()
    test_angular_suppression_off_center()
    print("\nall stage-4 tests passed")
