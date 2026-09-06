#!/usr/bin/env python3
"""Regenerate ONLY the dev split of one shard, bit-identical to shard_generate.

Production shards lost their dev splits (ENOSPC-era cleanup) while their
manifests still claim them. Populations and RNG streams are deterministic,
and within a shard the splits are simulated in dict order (calibration,
dev, final), so replaying the calibration slice first restores the exact
simulator state the dev slice was produced with. The final slice comes
afterwards and cannot affect dev, so it is not replayed.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.build_task import DEV_EVENTS, select_layout
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
)
from benchmarks.JunoResBench.world_generator.populations import calibration_population, physics_population
from benchmarks.JunoResBench.world_generator.shard.shard_generate import _shard_slice, _simulate_slice


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--geometry-mode", choices=("juno", "uniform"), default="juno")
    parser.add_argument("--n-pmt", type=int, default=None)
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--probe-events-per-point", type=int, default=200)
    parser.add_argument("--controls", type=int, default=7680)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True, help="dev output dir (dev/ is created inside)")
    args = parser.parse_args()

    out = Path(args.out)
    destination = out / "dev"
    if (destination / "index.npz").exists():
        print(f"shard {args.shard} dev: already present, skipping", flush=True)
        return

    streams = np.random.SeedSequence(args.seed).spawn(5)
    seeds = [int(s.generate_state(1, dtype=np.uint64)[0]) for s in streams]
    calibration = calibration_population(seeds[0], args.calibration_events_per_point)
    dev = physics_population(args.task, seeds[1], args.probe_events_per_point, args.controls)
    dev = {key: value[:DEV_EVENTS] for key, value in dev.items()}

    config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
    layout = select_layout(args.geometry_mode, args.n_pmt, JUNO_LPMT_CSV, JUNO_LPMT_TYPE_CSV)
    event_seed = int(np.random.default_rng([seeds[3], args.shard]).integers(0, 2**63))
    simulator = DetectorSim(config, layout, seed=seeds[4], event_seed=event_seed)
    noise_rng = np.random.default_rng([event_seed, 0x5EED])

    replay = out / "_calib_replay"
    cal_lo, cal_hi = _shard_slice(len(calibration["evt_e_true"]), args.shard, args.shards)
    if cal_hi > cal_lo:
        _simulate_slice(calibration, simulator, layout, replay, cal_lo, cal_hi, noise_rng)
        shutil.rmtree(replay)

    lo, hi = _shard_slice(len(dev["evt_e_true"]), args.shard, args.shards)
    _simulate_slice(dev, simulator, layout, destination, lo, hi, noise_rng)
    (out / "shard_manifest_dev.json").write_text(json.dumps(
        {"task": args.task, "seed": args.seed, "shard": args.shard,
         "shards": args.shards, "splits": {"dev": [int(lo), int(hi), int(hi - lo)]}},
        indent=1) + "\n", encoding="utf-8")
    for path in sorted(out.rglob("*")):
        if path.is_file():
            with open(path, "rb") as fh:
                os.fsync(fh.fileno())
    print(f"shard {args.shard} dev: events [{lo}:{hi}) -> {destination}", flush=True)


if __name__ == "__main__":
    main()
