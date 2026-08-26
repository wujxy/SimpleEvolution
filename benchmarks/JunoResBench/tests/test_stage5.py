"""Stage 5 unit tests: afterpulses, per-PMT time offsets, truth isolation.

Run: python3 benchmarks/JunoResBench/tests/test_stage5.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout


def _count_peaks(adc, threshold_sigma=5):
    """Crude peak count: samples below (baseline - k*sigma) forming runs."""
    base = int(np.median(adc))
    sigma = np.std(adc[:100])
    below = adc < base - threshold_sigma * sigma
    return int(np.sum(below[1:] & ~below[:-1]))   # falling edges


def test_afterpulses_present():
    """With exaggerated AP probability, extra pulses appear in waveforms."""
    lay = PMTLayout.uniform()
    cfg_off = DetectorConfig(afterpulse_prob=0.0)
    cfg_on = DetectorConfig(afterpulse_prob=0.5)
    n_off = n_on = 0
    for i in range(30):
        e0 = DetectorSim(cfg_off, lay, seed=100 + i).generate(0, 0, 0, 3.0)
        e1 = DetectorSim(cfg_on, lay, seed=100 + i).generate(0, 0, 0, 3.0)
        n_off += sum(_count_peaks(a) for a in e0.adc)
        n_on += sum(_count_peaks(a) for a in e1.adc)
    assert n_on > 1.3 * n_off, f"AP pulses missing ({n_on} vs {n_off})"
    print(f"ok  afterpulses visible: peak-counts AP-on {n_on} vs off {n_off} "
          f"({n_on/max(n_off,1):.2f}x)")


def test_afterpulse_truth_isolation():
    """AP on/off leaves the physics truth untouched."""
    lay = PMTLayout.uniform()
    e0 = DetectorSim(DetectorConfig(afterpulse_prob=0.0), lay, seed=7).generate(
        1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    e1 = DetectorSim(DetectorConfig(afterpulse_prob=0.3), lay, seed=7).generate(
        1.0, 2.0, -1.0, 3.0, with_waveforms=False)
    assert e1.n_pe_total == e0.n_pe_total
    assert np.array_equal(e1.q_pe, e0.q_pe)
    assert np.array_equal(e1.t_rel_ns, e0.t_rel_ns)
    print("ok  AP truth isolation (physics arrays bit-identical)")


def test_time_offset_effect():
    """Per-PMT offsets widen the post-TOF/emission residual."""
    lay = PMTLayout.uniform()
    e0 = DetectorSim(DetectorConfig(time_offset_sigma_ns=0.0), lay, seed=9).generate(
        0, 0, 0, 1.0, with_waveforms=False)
    e1 = DetectorSim(DetectorConfig(time_offset_sigma_ns=5.0), lay, seed=9).generate(
        0, 0, 0, 1.0, with_waveforms=False)
    res0 = e0.t_rel_ns - e0.t_emit_ns - e0.t_tof_ns - 300.0
    res1 = e1.t_rel_ns - e1.t_emit_ns - e1.t_tof_ns - 300.0
    expect1 = np.sqrt(4.0**2 + 5.0**2)   # ~6.40 ns
    assert abs(res1.std() - expect1) < 0.4, f"{res1.std():.2f} vs {expect1:.2f}"
    assert res1.std() > res0.std() + 1.5
    print(f"ok  time offsets: residual {res0.std():.2f} -> {res1.std():.2f} ns "
          f"(expect ~{expect1:.2f} at sigma_offset=5)")


def test_default_config_anchor():
    """Defaults: AP 1.6%, offset 1 ns; residual ~ TTS (+small)."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(DetectorConfig(), lay, seed=11)
    ev = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
    res = ev.t_rel_ns - ev.t_emit_ns - ev.t_tof_ns - 300.0
    assert 3.9 < res.std() < 4.5
    assert sim.calib.time_offset_ns.std() > 0.5
    print(f"ok  defaults: residual {res.std():.2f} ns, "
          f"offset spread {sim.calib.time_offset_ns.std():.2f} ns")


if __name__ == "__main__":
    test_afterpulses_present()
    test_afterpulse_truth_isolation()
    test_time_offset_effect()
    test_default_config_anchor()
    print("\nall stage-5 tests passed")
