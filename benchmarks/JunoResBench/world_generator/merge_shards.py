#!/usr/bin/env python3
"""Merge sharded build_task.py outputs into one release tree.

All shards must share the same --detector-seed (same per-tube calibration
constants and layout) and differ only in --seed (populations and per-event
streams). Splits are concatenated in shard order; the electron oracle vertex
threshold is recomputed on the merged final-split population.

Usage:
    python merge_shards.py --task electron_single_site \
        --out MERGED shard0 shard1 ...
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import (
    DetectorConfig,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
    PMTLayout,
)
from benchmarks.JunoResBench.world_generator.oracle_vertex import (
    charge_pattern_vertex_rms,
    freeze_threshold,
)

SPLITS = (
    ("public", "calibration"),
    ("public", "dev"),
    ("private", "final_observations"),
)


def _merge_split(shard_roots, side, name, destination):
    """Concatenate one sparse split across shards, streaming sample bytes."""
    destination.mkdir(parents=True, exist_ok=True)
    event_offsets = [0]
    sample_offsets = [0]
    segment_ids = []
    segment_starts = []
    metadatas = []
    for root in shard_roots:
        split = root / side / name
        with np.load(split / "index.npz", allow_pickle=False) as index:
            ev_off = index["event_segment_offsets"]
            sa_off = index["segment_sample_offsets"]
            segment_ids.append(index["segment_pmt_ids"])
            segment_starts.append(index["segment_start_samples"])
        # skip each shard's leading zero, shift by the running totals
        event_offsets.extend((ev_off[1:] + event_offsets[-1]).tolist())
        sample_offsets.extend((sa_off[1:] + sample_offsets[-1]).tolist())
        metadatas.append(
            json.loads((split / "metadata.json").read_text(encoding="utf-8"))
        )

    total = sample_offsets[-1]
    with (destination / "segment_samples.npy").open("wb") as target:
        np.lib.format.write_array_header_2_0(
            target, {"descr": "<i2", "fortran_order": False, "shape": (total,)}
        )
        for root in shard_roots:
            with (root / side / name / "segment_samples.npy").open("rb") as source:
                version = np.lib.format.read_magic(source)
                if version == (1, 0):
                    np.lib.format.read_array_header_1_0(source)
                else:
                    np.lib.format.read_array_header_2_0(source)
                shutil.copyfileobj(source, target, length=1 << 24)

    np.savez_compressed(
        destination / "index.npz",
        event_segment_offsets=np.asarray(event_offsets, dtype=np.int64),
        segment_sample_offsets=np.asarray(sample_offsets, dtype=np.int64),
        segment_pmt_ids=np.concatenate(segment_ids),
        segment_start_samples=np.concatenate(segment_starts),
    )

    metadata = metadatas[0]
    for other in metadatas[1:]:
        shared = {k: v for k, v in other.items() if k != "n_events"}
        base = {k: v for k, v in metadata.items() if k != "n_events"}
        if shared != base:
            raise ValueError(f"shard metadata mismatch in {side}/{name}")
    metadata["n_events"] = event_offsets[-1]
    (destination / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _merge_npz(shard_roots, relative, destination):
    """Concatenate per-event npz arrays, keeping step_offsets cumulative."""
    accumulated = {}
    step_shift = 0
    for root in shard_roots:
        path = root / relative
        if not path.exists():
            continue
        with np.load(path, allow_pickle=False) as pack:
            for key in pack.files:
                value = pack[key]
                if key == "step_offsets":
                    raw_last = int(value[-1]) if value.size else 0
                    value = value + step_shift
                    if accumulated.get(key):
                        value = value[1:]  # drop duplicated leading zero
                    step_shift += raw_last
                accumulated.setdefault(key, []).append(value)
    if not accumulated:
        return
    merged = {key: np.concatenate(parts) for key, parts in accumulated.items()}
    np.savez_compressed(destination, **merged)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("shards", nargs="+")
    args = parser.parse_args()

    shard_roots = [Path(shard) for shard in args.shards if not Path(shard).suffix]
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {out}")
    (out / "public").mkdir(parents=True)
    (out / "private").mkdir()

    for side, name in SPLITS:
        _merge_split(shard_roots, side, name, out / side / name)
    _merge_npz(shard_roots, Path("public/calibration/labels.npz"), out / "public/calibration/labels.npz")
    _merge_npz(shard_roots, Path("public/dev/truth.npz"), out / "public/dev/truth.npz")
    _merge_npz(shard_roots, Path("private/truth.npz"), out / "private/truth.npz")

    first = shard_roots[0]
    shutil.copy(
        first / "public/detector_geometry.npz", out / "public/detector_geometry.npz"
    )

    evaluation = {"energy_target_r_1mev": 0.03}
    if args.task == "electron_single_site":
        layout = PMTLayout.from_juno_csv(JUNO_LPMT_CSV, JUNO_LPMT_TYPE_CSV)
        config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
        with np.load(out / "private/truth.npz", allow_pickle=False) as truth:
            mask = (truth["evt_sample_role"] == 0) & (truth["evt_e_true"] == 1.0)
            vertices = truth["evt_vertex_m"][mask]
        oracle_rms = charge_pattern_vertex_rms(vertices, layout, config)
        threshold = freeze_threshold(oracle_rms)
        (out / "private/electron_oracle.json").write_text(
            json.dumps(
                {
                    "method": "ideal_charge_pattern_fisher",
                    "oracle_vertex_rms_m": oracle_rms,
                    "vertex_threshold_m": threshold,
                    "merged_from_shards": len(shard_roots),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        evaluation["vertex_threshold_m"] = threshold
    (out / "public/evaluation_config.json").write_text(
        json.dumps(evaluation, indent=2) + "\n", encoding="utf-8"
    )
    print(f"merged {len(shard_roots)} shards into {out}")


if __name__ == "__main__":
    main()
