"""Drift-model tests: OU statistics, determinism, run-clock wiring.

Run: python3 benchmarks/JunoResBench/tests/test_drift.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.drift import DriftState
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout


def test_ou_stationary_sigma():
    """Long-run OU spread matches the configured stationary sigma."""
    cfg = DetectorConfig(drift=True)
    st = DriftState(cfg, n_pmt=4096, seed=3)
    for k in range(200):
        st.advance(1.0 + k * 30.0)
    assert abs(st.ou_gain.std() - cfg.drift_gain_ou_sigma) < 0.005
    assert abs(st.ou_pde.std() - cfg.drift_pde_ou_sigma) < 0.003
    assert abs(st.ou_dcr) < 4 * cfg.drift_dcr_log_sigma
    print(f"ok  OU stationary: gain std {st.ou_gain.std():.4f} "
          f"(cfg {cfg.drift_gain_ou_sigma}), pde {st.ou_pde.std():.4f} "
          f"(cfg {cfg.drift_pde_ou_sigma}), dcr scale {st.dark_rate_scale:.3f}")


def test_state_moves_on_run_clock():
    """Half a correlation time apart, the per-PMT state differs materially
    but stays inside its stationary spread."""
    cfg = DetectorConfig(drift=True)
    a = DriftState(cfg, n_pmt=4096, seed=11)
    b = DriftState(cfg, n_pmt=4096, seed=11)
    a.advance(0.0)
    a.advance(cfg.drift_gain_ou_tau_s / 2.0)
    b.advance(0.0)
    d = a.ou_gain - b.ou_gain
    # var(diff) = 2 sigma^2 (1 - e^{-dt/tau}) for exact OU
    want = cfg.drift_gain_ou_sigma * np.sqrt(
        2 * (1 - np.exp(-0.5)))
    assert 0.6 * want < d.std() < 1.4 * want, f"OU move {d.std():.4f} vs {want:.4f}"
    print(f"ok  run clock: |gain(t+tau/2)-gain(t)| std {d.std():.4f} "
          f"(exact-OU expectation {want:.4f})")


def test_generate_deterministic_with_drift():
    """Same seed + same run times -> bit-identical waveforms (drift draws
    live in their own reproducible stream)."""
    lay = PMTLayout.uniform(n_pmt=2048, radius_m=19.365)
    times = [7.1, 82.0, 613.4, 1811.9, 3602.5]

    def run():
        sim = DetectorSim(DetectorConfig(drift=True), lay, seed=99)
        return [sim.generate(0.5, -0.3, 0.1, 3.0, run_time_s=t) for t in times]

    ea, eb = run(), run()
    assert all(np.array_equal(x.adc_ids, y.adc_ids) for x, y in zip(ea, eb))
    assert all(np.array_equal(x.n_pe_pmt, y.n_pe_pmt) for x, y in zip(ea, eb))
    assert all(np.array_equal(np.stack(x.adc), np.stack(y.adc))
               for x, y in zip(ea, eb))
    print(f"ok  drift determinism: {len(times)} events bit-identical "
          f"across two runs")


def test_drift_requires_run_time():
    """cfg.drift on + no run_time_s -> loud error, not silent no-drift."""
    sim = DetectorSim(DetectorConfig(drift=True),
                      PMTLayout.uniform(n_pmt=512), seed=5)
    try:
        sim.generate(0.0, 0.0, 0.0, 2.0)
    except ValueError as e:
        assert "run_time_s" in str(e)
        print("ok  drift without run_time_s raises ValueError")
    else:
        raise AssertionError("missing run_time_s was silently accepted")


def test_drift_off_unchanged():
    """drift=False (default): generate() without run_time_s is the frozen
    v1 stream — byte-identical to the pre-drift code path."""
    lay = PMTLayout.uniform(n_pmt=2048, radius_m=19.365)
    ev = DetectorSim(DetectorConfig(), lay, seed=7).generate(1.0, 2.0, -1.0, 3.0)
    ev2 = DetectorSim(DetectorConfig(), lay, seed=7).generate(1.0, 2.0, -1.0, 3.0)
    assert all(np.array_equal(a, b) for a, b in zip(ev.adc, ev2.adc))
    print(f"ok  drift off: default stream deterministic ({len(ev.adc)} rows)")


if __name__ == "__main__":
    test_ou_stationary_sigma()
    test_state_moves_on_run_clock()
    test_generate_deterministic_with_drift()
    test_drift_requires_run_time()
    test_drift_off_unchanged()
    print("drift tests: all ok")
