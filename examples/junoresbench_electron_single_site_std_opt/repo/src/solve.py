"""Baseline: integrated sparse charge and calibrated charge centroid."""

import argparse
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks/electron_single_site/evaluator"))
from sparse_reader import SparseSplit  # noqa: E402


def features(split_path, positions):
    split = SparseSplit(split_path)
    charge = np.zeros(len(split), dtype=float)
    centroid = np.zeros((len(split), 3), dtype=float)
    for event_index, event in enumerate(split.iter_events()):
        if not len(event.segment_pmt_ids):
            continue
        pulse = np.maximum(-np.asarray(event.samples, dtype=np.int32), 0)
        segment_charge = np.add.reduceat(
            pulse, np.asarray(event.segment_sample_offsets[:-1], dtype=np.int64)
        ).astype(float)
        total = float(segment_charge.sum())
        charge[event_index] = total
        if total > 0:
            centroid[event_index] = (
                positions[event.segment_pmt_ids] * segment_charge[:, None]
            ).sum(axis=0) / total
    return charge, centroid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    geometry = args.geometry or args.calibration.parent / "detector_geometry.npz"
    with np.load(geometry, allow_pickle=False) as payload:
        positions = np.asarray(payload["pmt_positions_m"], dtype=float)
    with np.load(args.calibration / "labels.npz", allow_pickle=False) as labels:
        energy_cal = np.asarray(labels["source_energy_mev"], dtype=float)
        vertex_cal = np.asarray(labels["deployment_position_m"], dtype=float)

    charge_cal, centroid_cal = features(args.calibration, positions)
    charge, centroid = features(args.data, positions)
    energy_design = np.column_stack((charge_cal, np.ones(len(charge_cal))))
    energy_map, *_ = np.linalg.lstsq(energy_design, energy_cal, rcond=None)
    if not np.isfinite(energy_map).all():
        raise ValueError("calibration charge is degenerate for energy fit")
    design_cal = np.column_stack((centroid_cal, np.ones(len(centroid_cal))))
    vertex_map, *_ = np.linalg.lstsq(design_cal, vertex_cal, rcond=None)
    vertex = np.column_stack((centroid, np.ones(len(centroid)))) @ vertex_map

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        args.out,
        E_rec=np.column_stack((charge, np.ones(len(charge)))) @ energy_map,
        x_rec=vertex[:, 0],
        y_rec=vertex[:, 1],
        z_rec=vertex[:, 2],
    )
    print(f"wrote {args.out} ({len(charge)} events)")


if __name__ == "__main__":
    main()
