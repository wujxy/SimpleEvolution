#!/usr/bin/env python3
"""Publish a shard bank: index the shards and write the release documents.

The shards ARE the release — publishing is zero copy. It writes down where
the shards are and in which order, plus the small owner-side documents:

  public/release.json            ordered calibration+dev shard index (agent-facing)
  public/calibration/labels.npz  source energies + deployment positions
  public/evaluation_config.json  frozen gates and targets
  private/final.json             ordered final shard index with per-shard truth
  private/electron_oracle.json   frozen vertex oracle record

Every split's index and payload are stat-verified (npy header + byte count).
No waveform content is hashed, read, or rewritten: the generator attests a
split by writing shard_manifest.json only after its own byte check, and the
validator samples real events at accept time.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
)
from benchmarks.JunoResBench.world_generator.oracle_vertex import (
    charge_pattern_vertex_rms,
    freeze_gate,
    freeze_threshold,
)
from benchmarks.JunoResBench.world_generator.build_task import (
    ENERGY_BIAS_1MEV_MAX,
    ENERGY_BIAS_MAX,
    ENERGY_RESOLUTION_GATE,
    VERTEX_HIGH_ENERGY_RMS_RATIO_MAX,
    VERTEX_RADIAL_BIAS_MAX_M,
    select_layout,
)

SPLITS = ("calibration", "dev", "final")


def _shard_roots(shards_roots):
    roots = []
    for base in shards_roots:
        roots.extend(p for p in Path(base).iterdir()
                     if p.is_dir() and p.name.startswith("shard_"))
    return sorted(roots, key=lambda p: int(p.name.split("_")[1]))


def _verify_split(src):
    """Stat-verify one split (index arrays, npy header, payload bytes)."""
    for name in ("metadata.json", "index.npz", "segment_samples.npy"):
        if not (src / name).is_file():
            raise IOError(f"{src}: missing {name}")
    with np.load(src / "index.npz", allow_pickle=False) as ix:
        events = int(len(ix["event_segment_offsets"]) - 1)
        declared = int(ix["segment_sample_offsets"][-1])
        n_segment = len(ix["segment_pmt_ids"])
        if (int(ix["event_segment_offsets"][0]) != 0
                or int(ix["event_segment_offsets"][-1]) != n_segment
                or ix["segment_sample_offsets"].shape != (n_segment + 1,)
                or int(ix["segment_sample_offsets"][0]) != 0):
            raise IOError(f"{src}: inconsistent index arrays")
    payload = src / "segment_samples.npy"
    with payload.open("rb") as fh:
        version = np.lib.format.read_magic(fh)
        if version == (1, 0):
            shape, _, _ = np.lib.format.read_array_header_1_0(fh)
        elif version == (2, 0):
            shape, _, _ = np.lib.format.read_array_header_2_0(fh)
        else:
            raise IOError(f"{src}: unsupported npy version {version}")
        data_start = fh.tell()
    if shape[0] != declared or payload.stat().st_size - data_start != declared * 2:
        raise IOError(
            f"{src}: payload holds {payload.stat().st_size - data_start} bytes, "
            f"npy header declares {shape[0] * 2}, index declares {declared * 2}"
            " — regenerate this shard")
    return events


def _load_truth_entries(shard_roots, split):
    """Concatenate the small per-shard truth arrays of one split, in order."""
    blocks = {}
    for root in shard_roots:
        path = root / split / "truth.npz"
        if not path.is_file():
            raise IOError(f"{path}: missing truth for split {split}")
        with np.load(path, allow_pickle=False) as data:
            for key in data.files:
                blocks.setdefault(key, []).append(data[key])
    return {k: np.concatenate(v) for k, v in blocks.items()}


def publish_release(shard_roots, out, task, config, layout):
    public = out / "public"
    private = out / "private"
    roots = _shard_roots(shard_roots)
    if not roots:
        raise IOError("no shard_* directories found")

    populations = {}
    for split in SPLITS:
        entries = []
        for root in roots:
            src = root / split
            if not (src / "index.npz").is_file():
                raise IOError(f"{src}: shard {root.name} has no {split} split")
            events = _verify_split(src)
            entry = {
                "shard": root.name,
                "index": str((src / "index.npz").resolve()),
                "events": events,
            }
            truth = src / "truth.npz"
            if truth.is_file():
                entry["truth"] = str(truth.resolve())
            entries.append(entry)
        populations[split] = {"n_events": sum(e["events"] for e in entries),
                              "shards": entries}

    (public / "calibration").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        public / "detector_geometry.npz", pmt_positions_m=layout.positions_m)
    (private).mkdir(parents=True, exist_ok=True)
    labels_truth = _load_truth_entries(roots, "calibration")
    np.savez_compressed(
        public / "calibration" / "labels.npz",
        source_energy_mev=labels_truth["evt_e_true"],
        deployment_position_m=labels_truth["evt_vertex_m"],
    )

    evaluation = {
        "energy_target_r_1mev": 0.03,
        "energy_resolution_gate": ENERGY_RESOLUTION_GATE,
        "energy_bias_1mev_abs_max": ENERGY_BIAS_1MEV_MAX,
        "energy_bias_abs_max": ENERGY_BIAS_MAX,
        "vertex_radial_bias_abs_max_m": VERTEX_RADIAL_BIAS_MAX_M,
        "vertex_high_energy_rms_ratio_max": VERTEX_HIGH_ENERGY_RMS_RATIO_MAX,
    }
    if task == "electron_single_site":
        final = _load_truth_entries(roots, "final")
        vertices = final["evt_vertex_m"][
            (final["evt_sample_role"] == 0) & (final["evt_e_true"] == 1.0)]
        oracle_rms = charge_pattern_vertex_rms(vertices, layout, config)
        (private / "electron_oracle.json").write_text(json.dumps(
            {"method": "ideal_charge_pattern_fisher",
             "oracle_vertex_rms_m": oracle_rms,
             "vertex_rms_reference_m": freeze_threshold(oracle_rms),
             "vertex_resolution_gate_m": freeze_gate(oracle_rms)}, indent=2) + "\n",
            encoding="utf-8")
        evaluation["vertex_rms_reference_m"] = freeze_threshold(oracle_rms)
        evaluation["vertex_resolution_gate_m"] = freeze_gate(oracle_rms)

    (public / "evaluation_config.json").write_text(
        json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    (public / "release.json").write_text(json.dumps(
        {"task": task,
         "populations": {"calibration": populations["calibration"],
                         "dev": populations["dev"]}}, indent=1) + "\n",
        encoding="utf-8")
    (private / "final.json").write_text(json.dumps(
        {"task": task, "shards": populations["final"]["shards"]}, indent=1) + "\n",
        encoding="utf-8")
    return populations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--geometry-mode", choices=("juno", "uniform"), default="juno")
    parser.add_argument("--n-pmt", type=int, default=None)
    parser.add_argument("--shards-root", action="append", required=True,
                        help="directory holding shard_* dirs (repeatable)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
    layout = select_layout(args.geometry_mode, args.n_pmt, JUNO_LPMT_CSV, JUNO_LPMT_TYPE_CSV)
    populations = publish_release(args.shards_root, Path(args.out), args.task, config, layout)
    for split, population in populations.items():
        print(f"{split}: {population['n_events']} events across "
              f"{len(population['shards'])} shards")


if __name__ == "__main__":
    main()
