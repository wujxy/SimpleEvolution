#!/usr/bin/env python3
"""Shard the generation of a two-tier release dataset across workers.

Each shard regenerates the FULL deterministic populations (same master
seeds as build_task.build: populations are pure numpy draws, cheap), takes
a contiguous slice of events for each population, and simulates only its
slice with per-event RNG streams (detector.generate(..., event_index=i,
stream=k)). Waveforms stream to disk exactly as in build_task; per-shard
truth is small. merge_release.py later concatenates shards in order into
the release public/private trees — identical populations, same detector,
events statistically independent and order-free.

Population stream ids match build_task spawn order:
  0 calibration, 1 dev, 2 final.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.sparse_waveforms import SparseSplitWriter, encode_dense_event
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import PARTICLE_CODE_TYPE
from benchmarks.JunoResBench.world_generator.build_task import DEV_EVENTS, PUBLIC_METADATA, _metadata, select_layout
from benchmarks.JunoResBench.world_generator.populations import calibration_population, physics_population

def _shard_slice(total, shard, shards):
    """Contiguous [lo, hi) covering `total` events across `shards` parts."""
    base, extra = divmod(total, shards)
    lo = shard * base + min(shard, extra)
    hi = lo + base + (1 if shard < extra else 0)
    return lo, hi


def _simulate_slice(population, simulator, layout, destination, lo, hi, noise_rng):
    """Simulate population[lo:hi] into `destination` (shard-local streams)."""
    writer = SparseSplitWriter(destination)
    truth_rows = {key: [] for key in ("evt_e_vis", "evt_e_dep_mev", "evt_e_escape_mev", "evt_total_energy")}
    step_rows = {key: [] for key in ("step_pos_m", "step_e_dep_mev", "step_e_vis_mev", "step_dedx_mev_cm", "step_kinetic_mev", "step_length_m", "step_kind")}
    offsets = [0]
    t_start = time.time()
    for index in range(lo, hi):
        event = simulator.generate(
            *population["evt_vertex_m"][index], float(population["evt_e_true"][index]),
            t0_ns=float(population["evt_t0_ns"][index]),
            direction=tuple(population["evt_direction"][index]),
            particle_type=PARTICLE_CODE_TYPE[int(population["evt_particle_type"][index])],
        )
        adc = np.asarray(event.adc, dtype=np.uint16).reshape(-1, simulator.wave_cfg.n_samples)
        adc_ids = np.asarray(event.adc_ids, dtype=np.int64).ravel()
        # the digitizer emits hit/dark channels only; a full waveform readout
        # completes every quiet channel with a baseline-plus-white-noise row
        # drawn from the same electronics noise model as the stored rows
        n_samples = simulator.wave_cfg.n_samples
        full = np.full((layout.n_pmt, n_samples),
                       simulator.wave_cfg.baseline_adc, dtype=np.float64)
        full[adc_ids] = adc
        quiet = np.ones(layout.n_pmt, dtype=bool)
        quiet[adc_ids] = False
        if quiet.any():
            sigma_adc = (simulator.wave_cfg.noise_sigma_mv * 1e-3
                         / simulator.wave_cfg.lsb_v)
            full[quiet] += noise_rng.normal(
                0.0, sigma_adc, (int(quiet.sum()), n_samples))
        full = np.clip(np.rint(full), 0, 16383).astype(np.uint16)
        writer.append(encode_dense_event(
            full, np.arange(layout.n_pmt), simulator.wave_cfg.baseline_adc))
        truth_rows["evt_e_vis"].append(event.e_vis_mev)
        truth_rows["evt_e_dep_mev"].append(event.e_dep_mev)
        truth_rows["evt_e_escape_mev"].append(event.e_escape_mev)
        truth_rows["evt_total_energy"].append(
            float(population["evt_e_true"][index])
            + (1.021998 if population["evt_particle_type"][index] == 2 else 0.0))
        for key in step_rows:
            step_rows[key].append(getattr(event, key))
        offsets.append(offsets[-1] + len(event.step_e_dep_mev))
        if (index - lo + 1) % 50 == 0:
            rate = (index - lo + 1) / (time.time() - t_start)
            print(f"  {destination.name}[{lo}:{hi}] {index - lo + 1}/{hi - lo} ({rate:.1f}/s)", flush=True)
    truth = {key: np.asarray(value[lo:hi]) for key, value in population.items()}
    truth.update({key: np.asarray(value) for key, value in truth_rows.items()})
    truth["step_offsets"] = np.asarray(offsets, dtype=np.int64)
    for key, blocks in step_rows.items():
        truth[key] = np.concatenate(blocks) if blocks else np.empty(0)
    public_meta = {key: _metadata(simulator.cfg, layout, simulator)[key]
                   for key in PUBLIC_METADATA}
    writer.finalize(public_meta, truth=truth)
    return truth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--seed", type=int, required=True, help="master release seed (jobs.tsv seed)")
    parser.add_argument("--geometry-mode", choices=("juno", "uniform"), default="juno")
    parser.add_argument("--n-pmt", type=int, default=None,
                        help="only used with --geometry-mode uniform")
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--probe-events-per-point", type=int, default=200)
    parser.add_argument("--controls", type=int, default=7680)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--out", required=True, help="shard output dir (must not exist)")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # populations identical to build_task.build (seeds[0]=calib, [1]=dev, [2]=final)
    streams = np.random.SeedSequence(args.seed).spawn(5)
    seeds = [int(s.generate_state(1, dtype=np.uint64)[0]) for s in streams]
    calibration = calibration_population(seeds[0], args.calibration_events_per_point)
    dev = physics_population(args.task, seeds[1], args.probe_events_per_point, args.controls)
    dev = {key: value[:DEV_EVENTS] for key, value in dev.items()}
    final = physics_population(args.task, seeds[2], args.probe_events_per_point, args.controls)

    config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
    layout = select_layout(
        args.geometry_mode, args.n_pmt, JUNO_LPMT_CSV, JUNO_LPMT_TYPE_CSV
    )
    # seeds[4] = detector master seed, same as build_task: every shard builds
    # the SAME detector (calibration stays on the `seed` stream), while the
    # per-shard `event_seed` gives each worker its own independent physics and
    # electronics streams — statistically identical to one sequential run
    event_seed = int(np.random.default_rng([seeds[3], args.shard]).integers(0, 2**63))
    simulator = DetectorSim(config, layout, seed=seeds[4], event_seed=event_seed)
    noise_rng = np.random.default_rng([event_seed, 0x5EED])

    populations = {
        "calibration": calibration,
        "dev": dev,
        "final": final,
    }
    manifest = {"task": args.task, "seed": args.seed, "shard": args.shard,
                "shards": args.shards, "splits": {}}
    for name, population in populations.items():
        lo, hi = _shard_slice(len(population["evt_e_true"]), args.shard, args.shards)
        if hi <= lo:
            manifest["splits"][name] = [lo, hi, 0]
            continue
        destination = out / name
        if (destination / "index.npz").exists():
            print(f"shard {args.shard} {name}: already finalized, skipping", flush=True)
            manifest["splits"][name] = [int(lo), int(hi), int(hi - lo)]
            continue
        _simulate_slice(population, simulator, layout, destination, lo, hi, noise_rng)
        manifest["splits"][name] = [int(lo), int(hi), int(hi - lo)]
        print(f"shard {args.shard}/{args.shards} {name}: events [{lo}:{hi}) -> {destination}", flush=True)
    (out / "shard_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    # flush every artifact to the lustre server before the job exits: the
    # manifest can otherwise become visible to the merging client before the
    # (large) sample streams do, and a concurrent merge reads truncated files
    for path in sorted(out.rglob("*")):
        if path.is_file():
            with open(path, "rb") as fh:
                os.fsync(fh.fileno())
    for directory in (out, *({p for p in out.rglob("*") if p.is_dir()})):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    print(f"shard {args.shard} done", flush=True)


if __name__ == "__main__":
    main()
