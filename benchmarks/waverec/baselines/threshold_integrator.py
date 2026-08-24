#!/usr/bin/env python3
"""Baseline waveform reconstructor: threshold + integral charge + peak time.

This is a deliberately simple, COTI-like (Continuous Over-Threshold
Integral) reference algorithm. It is NOT part of the truth — it exists so
that any future agent/algorithm has a number to beat, and so the dataset
format has a demonstrated consumer.

For each event:
  1. estimate baseline from the first 100 samples (median)
  2. walk the waveform; a pulse opens when |adc - baseline| > k * sigma
     (sigma from the same baseline window) and closes after m samples back
     under threshold
  3. pulse charge  = sum of (baseline - adc) over the window, in ADC counts
                    -> convert to pe with the known template integral
     pulse time    = time of the minimum sample in the window (leading-edge
                     time can be substituted; peak time is simplest)

Writes predictions as an .npz with the same ragged layout as the dataset.
"""

import argparse
import dataclasses
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavegen import WaveGenConfig, WaveformGenerator  # noqa: E402


def reconstruct(
    adc: np.ndarray,
    cfg: WaveGenConfig,
    k_sigma: float = 5.0,
    close_samples: int = 8,
    min_gap_samples: int = 5,
) -> tuple:
    """Return (times_ns, amplitudes_pe) for one waveform."""
    bl_win = adc[:100].astype(float)
    baseline = float(np.median(bl_win))
    sigma = float(np.std(bl_win)) + 1e-6
    x = baseline - adc.astype(float)  # positive-going pulse height

    thr = k_sigma * sigma
    times, charges = [], []

    n = len(x)
    i = 0
    while i < n:
        if x[i] > thr:
            # pulse opens; extend while above threshold or within close gap
            j = i
            last_over = i
            while j < n:
                if x[j] > thr:
                    last_over = j
                    j += 1
                elif j - last_over < close_samples:
                    j += 1
                else:
                    break
            window = x[i : last_over + 1]
            q_counts = float(window.sum())
            t_peak = i + int(np.argmin(adc[i : last_over + 1]))
            times.append(t_peak * cfg.sample_interval_ns)
            charges.append(q_counts)
            i = last_over + close_samples
        else:
            i += 1

    counts_per_pe = template_area_counts(cfg)
    amplitudes = np.asarray(charges) / counts_per_pe
    return np.asarray(times, dtype=np.float64), amplitudes


def template_area_counts(cfg: WaveGenConfig) -> float:
    """Integrated |adc deviation| of a 1-pe pulse, in counts (cached by cfg)."""
    tmpl = WaveformGenerator(cfg, seed=0)._template  # peak-normalized volts
    area_v_ns = float(np.abs(tmpl).sum()) * cfg.sample_interval_ns
    return area_v_ns / cfg.lsb_v


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, help="input dataset .npz")
    p.add_argument("--out", required=True, help="output predictions .npz")
    p.add_argument("--k-sigma", type=float, default=5.0)
    args = p.parse_args()

    d = np.load(args.data, allow_pickle=False)
    meta = json.loads(str(d["meta"]))
    cfg_fields = {k: v for k, v in meta["config"].items() if k != "pulse_shape"}
    cfg_fields["pulse_shape"] = __import__("wavegen").PulseShape(
        meta["config"]["pulse_shape"]
    )
    cfg = WaveGenConfig(**cfg_fields)

    adc_all = d["adc"]
    n = adc_all.shape[0]
    counts = []
    times = []
    amps = []
    for row in adc_all:
        t, a = reconstruct(row, cfg, k_sigma=args.k_sigma)
        counts.append(len(t))
        times.append(t)
        amps.append(a)
    offsets = np.zeros(n + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(counts)
    t_flat = np.concatenate(times) if times else np.zeros(0)
    a_flat = np.concatenate(amps) if amps else np.zeros(0)

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        n_pred=np.asarray(counts, dtype=np.int32),
        t_offsets=offsets,
        t_pred=t_flat,
        a_pred=a_flat,
        meta=json.dumps({"data": str(args.data), "k_sigma": args.k_sigma}),
    )
    n_true = int(d["n_pe"].sum())
    print(f"wrote {out}: {sum(counts)} pulses predicted vs {n_true} true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
