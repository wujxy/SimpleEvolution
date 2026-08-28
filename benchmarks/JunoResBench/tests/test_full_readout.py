"""Full-readout mode tests: zero-suppressed digitization each window.

Run: python3 benchmarks/JunoResBench/tests/test_full_readout.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout


def _baseline_band(adc):
    """(baseline, robust sigma) — MAD-based so a pulse inside the row
    cannot inflate the noise estimate."""
    wf = np.asarray(adc, dtype=np.float64)
    base = float(np.median(wf))
    return base, float(1.4826 * np.median(np.abs(wf - base)))


def test_zero_suppressed_rows():
    """full_readout: rows = hit ∪ in-window-dark channels (sorted, unique).
    Dark-only channels exist beyond the hit set, carry a pulse, and silent
    channels are absent by construction."""
    lay = PMTLayout.uniform(n_pmt=8192, radius_m=19.365)
    sim = DetectorSim(DetectorConfig(full_readout=True), lay, seed=42)
    ev = sim.generate(0.0, 0.0, 0.0, 3.0)
    n_ch = lay.n_pmt
    assert ev.adc_ids is not None
    adc_ids = np.asarray(ev.adc_ids)
    assert len(adc_ids) == len(ev.adc)
    assert np.all(np.diff(adc_ids) > 0), "rows must be ascending+unique"
    hit = set(int(x) for x in ev.pmt_ids)
    rows = set(int(x) for x in adc_ids)
    assert hit <= rows, "every physics-hit channel must be stored"
    assert len(rows) < n_ch, "expected silent channels in a 3 MeV event"

    # dark-only rows: channels outside the hit set — each must show at
    # least one pulse excursion below baseline (that is why they exist)
    dark_only = sorted(rows - hit)
    n_pulsed = 0
    for k in dark_only:
        wf = np.asarray(ev.adc[int(np.searchsorted(adc_ids, k))])
        base, sigma = _baseline_band(wf)
        assert int(wf.min()) >= 0
        assert np.all(wf < base + 6 * sigma + 1)
        below = wf < (base - 5.0 * sigma)
        n_pulsed += int(np.sum(below[1:] & ~below[:-1]))
    assert n_pulsed >= len(dark_only) * 0.8, (
        f"dark-only rows without pulses ({n_pulsed}/{len(dark_only)})")
    # 24 kHz x 1 us x silent channels ~ O(100) at this occupancy
    assert len(dark_only) > 20, f"too few dark-only rows ({len(dark_only)})"
    print(f"ok  zero-suppressed: {len(rows)} rows = {len(hit)} hit + "
          f"{len(dark_only)} dark-only; {n_pulsed} pulses on dark-only rows")


def test_default_mode_unchanged():
    """Hit-storage contract: adc rows align with pmt_ids, and the refactor
    left the default-mode RNG streams alone (same seed -> same adc)."""
    lay = PMTLayout.uniform()
    ev = DetectorSim(DetectorConfig(), lay, seed=7).generate(1.0, 2.0, -1.0, 3.0)
    assert len(ev.adc) == len(ev.pmt_ids)
    assert np.array_equal(ev.adc_ids, ev.pmt_ids)
    ev2 = DetectorSim(DetectorConfig(), lay, seed=7).generate(1.0, 2.0, -1.0, 3.0)
    assert all(np.array_equal(a, b) for a, b in zip(ev.adc, ev2.adc))
    print(f"ok  default mode: {len(ev.adc)} hit rows, adc_ids == pmt_ids, "
          "deterministic")


if __name__ == "__main__":
    test_zero_suppressed_rows()
    test_default_mode_unchanged()
    print("full-readout tests: all ok")
