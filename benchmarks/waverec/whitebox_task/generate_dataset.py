#!/usr/bin/env python3
"""Generate the waverec benchmark dataset (waveforms + truth) as an .npz.

Usage:
    python3 scripts/generate_dataset.py --out data/waverec_v1.npz \
        --events 200 --seed 20260824

Dataset layout (npz keys):
    adc              (N, n_samples) int32   digitized waveforms
    n_pe             (N,)             int32  pulses per event
    t_hit_ns         list per event -> flat arrays with offsets:
    t_offsets        (N+1,)           int64  offsets into t_hits/amplitudes
    t_hits           (sum_npe,)       float64  hit times, ns from window start
    amplitudes       (sum_npe,)       float64  hit charges, pe
    plus a `meta` json string with the full WaveGenConfig + seed.
"""

import argparse
import dataclasses
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wavegen import WaveGenConfig  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, help="output .npz path")
    p.add_argument("--events", type=int, default=200)
    p.add_argument("--mean-pe", type=float, default=10.0)
    p.add_argument("--seed", type=int, default=20260824)
    p.add_argument("--noise-mv", type=float, default=None,
                   help="override noise_sigma_mv")
    p.add_argument("--n-samples", type=int, default=None,
                   help="override n_samples")
    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg_kwargs = {}
    if args.noise_mv is not None:
        cfg_kwargs["noise_sigma_mv"] = args.noise_mv
    if args.n_samples is not None:
        cfg_kwargs["n_samples"] = args.n_samples
    cfg = WaveGenConfig(**cfg_kwargs)

    from wavegen import WaveformGenerator

    gen = WaveformGenerator(cfg, seed=args.seed)
    events = gen.generate_batch(n_events=args.events, mean_pe=args.mean_pe)

    n = len(events)
    n_samples = events[0].adc.size
    adc = np.stack([e.adc for e in events]).astype(np.int32)
    n_pe = np.asarray([len(e.truth) for e in events], dtype=np.int32)
    offsets = np.zeros(n + 1, dtype=np.int64)
    offsets[1:] = np.cumsum(n_pe)
    t_hits = np.concatenate(
        [np.asarray([p.t_hit_ns for p in e.truth]) for e in events]
    )
    amps = np.concatenate(
        [np.asarray([p.amplitude_pe for p in e.truth]) for e in events]
    )

    meta = {
        "generator": "wavegen",
        "seed": args.seed,
        "events": n,
        "mean_pe": args.mean_pe,
        "config": dataclasses.asdict(cfg) | {
            "pulse_shape": cfg.pulse_shape.value,
        },
    }

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        adc=adc,
        n_pe=n_pe,
        t_offsets=offsets,
        t_hits=t_hits,
        amplitudes=amps,
        meta=json.dumps(meta),
    )
    total_pe = int(n_pe.sum())
    print(f"wrote {out}: {n} events x {n_samples} samples, {total_pe} PEs total")
    print(f"mean PE/event = {n_pe.mean():.2f} (requested {args.mean_pe})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
