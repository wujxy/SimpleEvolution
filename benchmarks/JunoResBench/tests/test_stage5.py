"""Stage 5 unit tests: afterpulses, per-PMT time offsets, truth isolation.

Run: python3 benchmarks/JunoResBench/tests/test_stage5.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout


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


def _rand_vertex(rng, rmax=14.0):
    """Isotropic vertex inside the LS sphere (generate() does not validate)."""
    r = rng.uniform(0.0, rmax ** 3) ** (1.0 / 3.0)
    ct = rng.uniform(-1.0, 1.0)
    phi = rng.uniform(0.0, 2.0 * np.pi)
    st = np.sqrt(1.0 - ct * ct)
    return (r * st * np.cos(phi), r * st * np.sin(phi), r * ct)


def test_trigger_causal_and_fires():
    """t_trigger sits after first light, within a bounded latency of t0."""
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s5_electronics import (
        _find_trigger,
    )
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()
    rng = np.random.default_rng(0)
    lat = []
    for i in range(15):
        t0 = float(rng.uniform(0, 1000))
        ev = DetectorSim(cfg, lay, seed=500 + i).generate(
            *_rand_vertex(rng), float(rng.uniform(1, 8)), t0_ns=t0,
            with_waveforms=False)
        first_light = float(ev.t_emit_ns.min() + ev.t_tof_ns.min())
        lat.append(ev.t_trigger_ns - ev.t0_ns)
        # causal: trigger at/after the earliest possible light (t0-relative
        # chain times; allow a small negative margin for TTS/offsets smearing
        # the true first arrival below the min over kept PEs)
        assert ev.t_trigger_ns - ev.t0_ns > first_light - 15.0
        assert ev.t_trigger_ns - ev.t0_ns < 400.0
    lat = np.asarray(lat)
    assert (lat > 0).all()
    assert lat.std() > 1.0, "trigger latency has no spread -> t0 task degenerate"
    # unit level: a pure-dark stream at the real total rate (~422 pe/us over
    # 17612 PMTs -> ~970 pe per 2.3-us span, ~42 per 100-ns window) must
    # never reach the 200-pe threshold
    drng = np.random.default_rng(1)
    fired = 0
    for _ in range(20):
        dark_only = drng.uniform(-800, 1500, 970)
        for t0 in (0.0, 730.5):
            if _find_trigger(dark_only + t0, t0, cfg) != t0:
                fired += 1
    assert fired == 0, f"pure dark fired the trigger {fired} times"
    print(f"ok  trigger: causal, latency {lat.mean():.0f}+-{lat.std():.0f} ns, "
          f"pure-dark never fires")


def test_t0_observable_and_invariant():
    """Window-referenced t0 varies event-to-event; absolute t0 does not.

    The trigger follows the event, so shifting ONLY t0 leaves the readout
    bit-identical (absolute event time is unobservable — score against the
    window-referenced t0 instead). What the waveform does encode is the
    trigger latency (t0 - t_trig), which varies with vertex/energy.
    """
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()
    a = DetectorSim(cfg, lay, seed=21).generate(1.0, -2.0, 3.0, 2.0, t0_ns=0.0)
    b = DetectorSim(cfg, lay, seed=21).generate(1.0, -2.0, 3.0, 2.0, t0_ns=730.5)
    assert np.array_equal(np.asarray(a.adc), np.asarray(b.adc))
    assert a.n_pe_total == b.n_pe_total
    assert abs((a.t_trigger_ns - a.t0_ns) - (b.t_trigger_ns - b.t0_ns)) < 1e-9

    rng = np.random.default_rng(2)
    t0_ref, radii, first_rel = [], [], []
    for i in range(20):
        t0 = float(rng.uniform(0, 1000))
        vtx = _rand_vertex(rng)
        ev = DetectorSim(cfg, lay, seed=700 + i).generate(
            *vtx, float(rng.uniform(1, 8)), t0_ns=t0, with_waveforms=False)
        t0_ref.append(t0 - (ev.t_trigger_ns - cfg.pre_trigger_ns))
        radii.append(np.linalg.norm(vtx))
        first_rel.append(float(ev.t_rel_ns.min()))
    t0_ref = np.asarray(t0_ref)
    spread = np.quantile(t0_ref, 0.84) - np.quantile(t0_ref, 0.16)
    assert spread > 5.0, f"window-referenced t0 spread {spread:.1f} ns too small"
    # The trigger cancels the vertex TOF in the observed light start, so t0_ref
    # is NOT tracked by the in-window first light (weak link via the threshold
    # accumulation only). It IS tracked by geometry: t0_ref = 300 - f - delta
    # with f = min TOF(vertex) ~ (R - r) * n / c — the t0 task therefore
    # couples to vertex reconstruction, exactly as in a real detector.
    r_rad = np.corrcoef(t0_ref, radii)[0, 1]
    assert r_rad > 0.8, f"corr(t0_ref, |vertex|) = {r_rad:.3f}"
    r_first = np.corrcoef(t0_ref, first_rel)[0, 1]
    assert -0.2 < r_first < 0.9
    print(f"ok  t0: absolute shift invariant; window-ref spread (q84-q16) "
          f"{spread:.0f} ns; corr with |vertex| {r_rad:.2f} "
          f"(geometric, strong); with first light {r_first:.2f} (weak, "
          f"trigger cancels TOF)")


def test_window_truncation():
    """PEs outside [t_trig-pre, +window) are dropped; truth stays consistent."""
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()
    rng = np.random.default_rng(3)
    dropped = 0
    for i in range(8):
        ev = DetectorSim(cfg, lay, seed=900 + i).generate(
            *_rand_vertex(rng, 15.5), float(rng.uniform(2, 8)),
            with_waveforms=False)
        dropped += ev.n_pe_produced - ev.n_pe_total
        assert ev.n_pe_total == len(ev.t_rel_ns) == len(ev.q_pe)
        assert ev.t_rel_ns.min() >= 0.0 and ev.t_rel_ns.max() < cfg.window_ns
    assert dropped > 0, "long emission/TOF tails should lose some PEs"
    print(f"ok  window truncation: {dropped} PEs dropped over 8 events")


if __name__ == "__main__":
    test_afterpulses_present()
    test_afterpulse_truth_isolation()
    test_time_offset_effect()
    test_default_config_anchor()
    test_trigger_causal_and_fires()
    test_t0_observable_and_invariant()
    test_window_truncation()
    print("\nall stage-5 tests passed")
