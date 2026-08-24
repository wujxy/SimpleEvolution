#!/usr/bin/env python3
"""Sanity checks for the wavegen forward model (run before trusting a dataset).

Verifies, on a fixed seed:
  1. determinism      — same (config, seed) -> identical waveforms
  2. truth-vs-samples — adc deviates from baseline only near true hits
  3. amplitude linearity — integrated charge scales with n_pe
  4. digitizer        — values within [0, 2^bits-1], baseline centered right
"""

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavegen import WaveGenConfig, WaveformGenerator  # noqa: E402


def main() -> int:
    ok = True

    # 1. determinism
    cfg = WaveGenConfig()
    g1 = WaveformGenerator(cfg, seed=42)
    g2 = WaveformGenerator(cfg, seed=42)
    e1 = g1.generate(channel_id=0, n_pe=5)
    e2 = g2.generate(channel_id=0, n_pe=5)
    same = np.array_equal(e1.adc, e2.adc)
    print(f"[{'OK' if same else 'FAIL'}] determinism: same seed -> identical adc")
    ok &= same

    # 2. signal appears at hit times
    ev = WaveformGenerator(cfg, seed=1).generate(channel_id=0, n_pe=3)
    bl = cfg.baseline_adc
    dev = np.abs(ev.adc.astype(float) - bl)
    thr = 5.0 * cfg.noise_sigma_mv * 1e-3 / cfg.lsb_v
    near_hits = 0
    for p in ev.truth:
        i = int(round(p.t_hit_ns / cfg.sample_interval_ns))
        window = dev[max(i - 30, 0) : i + 30]
        if window.max() > thr:
            near_hits += 1
    good = near_hits == len(ev.truth)
    print(f"[{'OK' if good else 'FAIL'}] {near_hits}/{len(ev.truth)} hits show "
          f">{thr:.1f}-sigma deviation within +-30 samples")
    ok &= good

    # 3. charge linearity: mean integrated charge per PE, n_pe=1 vs n_pe=10
    # (average over many events: single draws hit the SPE tail with large
    #  variance, so one-event ratios are meaningless; the |adc-bl| integral
    #  has a large noise floor, subtract it using empty events)
    gs = WaveformGenerator(cfg, seed=7)
    def raw_charge(e):
        return np.abs(e.adc.astype(float) - bl).sum()
    empty = [raw_charge(gs.generate(channel_id=0, n_pe=0)) for _ in range(40)]
    noise_floor = float(np.mean(empty))
    def mean_charge_per_pe(n_pe: int, n_events: int = 60) -> float:
        tot = 0.0
        for _ in range(n_events):
            e = gs.generate(channel_id=0, n_pe=n_pe)
            tot += max(raw_charge(e) - noise_floor, 0.0) / n_pe
        return tot / n_events
    q1 = mean_charge_per_pe(1)
    q10 = mean_charge_per_pe(10)
    ratio = q10 / q1
    good = 0.8 < ratio < 1.2
    print(f"[{'OK' if good else 'FAIL'}] charge linearity: per-PE charge "
          f"10pe/1pe = {ratio:.3f} (expected ~1; noise floor {noise_floor:.0f} "
          f"counts, q1 {q1:.0f}, q10 {q10:.0f})")
    ok &= good

    # 4. digitizer range / baseline
    gs4 = WaveformGenerator(cfg, seed=11)
    ev1 = gs4.generate(channel_id=0, n_pe=1)
    ev10 = gs4.generate(channel_id=0, n_pe=10)
    adc_max = (1 << cfg.adc_bits) - 1
    lo, hi = ev1.adc.min(), ev10.adc.max()
    in_range = 0 <= lo and hi <= adc_max
    bl_ok = abs(int(np.median(ev1.adc)) - bl) <= 3
    print(f"[{'OK' if in_range else 'FAIL'}] adc range [{lo}, {hi}] within "
          f"[0, {adc_max}]")
    print(f"[{'OK' if bl_ok else 'FAIL'}] baseline median {int(np.median(ev1.adc))} "
          f"~ configured {bl}")
    ok &= in_range and bl_ok

    print("ALL OK" if ok else "CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
