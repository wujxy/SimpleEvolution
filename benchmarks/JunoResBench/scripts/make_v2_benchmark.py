#!/usr/bin/env python3
"""Assemble the public and evaluator-private JunoResBench v2 package."""

import argparse
from pathlib import Path
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import write_sparse_split
from benchmarks.JunoResBench.scripts.generate_v2_dataset import (
    combine_populations,
    make_population,
    simulate_population,
)


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_METADATA_KEYS = {
    "layout",
    "n_pmt",
    "radius_m",
    "sample_interval_ns",
    "adc_bits",
    "window_ns",
}
DEV_TRUTH_KEYS = {"evt_sample_role", "evt_e_true", "evt_e_vis"}


def _public_metadata(metadata):
    return {key: metadata[key] for key in PUBLIC_METADATA_KEYS if key in metadata}


def _write_task_assets(task_directory):
    task_source = BENCHMARK_ROOT / "task_v2" / "TASK.md"
    if task_source.exists() and task_source.resolve() != (task_directory / "TASK.md").resolve():
        shutil.copyfile(task_source, task_directory / "TASK.md")
    elif not (task_directory / "TASK.md").exists():
        (task_directory / "TASK.md").write_text(
            "# JunoResBench v2\n\nThe public task contract is installed in Task 7.\n",
            encoding="utf-8",
        )
    shutil.copyfile(
        BENCHMARK_ROOT / "scripts" / "evaluate_v2.py",
        task_directory / "evaluate.py",
    )


def assemble_v2_package(
    output_root,
    detector_geometry,
    calibration,
    dev,
    final,
):
    """Write strict public/private boundaries from generated split bundles."""
    root = Path(output_root)
    task = root / "task_v2"
    private = root / "blind_truth_v2"
    task.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        task / "detector_geometry.npz",
        pmt_positions_m=np.asarray(detector_geometry, dtype=float),
    )
    write_sparse_split(
        task / "calibration",
        _public_metadata(calibration["metadata"]),
        calibration["observations"],
    )
    labels = calibration["labels"]
    if set(labels) != {"source_energy_mev", "deployment_position_m"}:
        raise ValueError("calibration labels contain an undocumented field")
    np.savez_compressed(task / "calibration" / "labels.npz", **labels)

    dev_truth = {
        key: value for key, value in dev["truth"].items() if key in DEV_TRUTH_KEYS
    }
    if set(dev_truth) != DEV_TRUTH_KEYS:
        raise ValueError("dev split is missing scoring truth")
    write_sparse_split(
        task / "dev",
        _public_metadata(dev["metadata"]),
        dev["observations"],
        truth=dev_truth,
    )

    write_sparse_split(
        private / "final_observations",
        _public_metadata(final["metadata"]),
        final["observations"],
    )
    np.savez_compressed(private / "truth.npz", **final["truth"])
    _write_task_assets(task)


def _physics_population(seed_probe, seed_control, seed_shuffle, probe_count, controls):
    probes = make_population(
        "probes", seed_probe, events_per_point=probe_count
    )
    continuous = make_population("controls", seed_control, events=controls)
    return combine_populations(probes, continuous, seed=seed_shuffle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="package parent directory")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--n-pmt", type=int, default=17612)
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--dev-probe-events-per-point", type=int, default=200)
    parser.add_argument("--dev-controls", type=int, default=12800)
    parser.add_argument("--final-probe-events-per-point", type=int, default=2000)
    parser.add_argument("--final-controls", type=int, default=12800)
    args = parser.parse_args()

    streams = np.random.SeedSequence(args.seed).spawn(9)
    seeds = [int(stream.generate_state(1, dtype=np.uint64)[0]) for stream in streams]
    calibration_population = make_population(
        "calibration", seeds[0], events_per_point=args.calibration_events_per_point
    )
    dev_population = _physics_population(
        seeds[1], seeds[2], seeds[3], args.dev_probe_events_per_point, args.dev_controls
    )
    final_population = _physics_population(
        seeds[4], seeds[5], seeds[6], args.final_probe_events_per_point, args.final_controls
    )

    config = DetectorConfig(optics_mode="trace", full_readout=True)
    layout = PMTLayout.uniform(args.n_pmt, config.detector_radius_m)
    simulator = DetectorSim(config, layout, seed=seeds[7])
    calibration = simulate_population(
        calibration_population, seeds[7], layout=layout, simulator=simulator
    )
    dev = simulate_population(dev_population, seeds[7], layout=layout, simulator=simulator)
    final = simulate_population(final_population, seeds[7], layout=layout, simulator=simulator)
    assemble_v2_package(args.out, layout.positions_m, calibration, dev, final)


if __name__ == "__main__":
    main()
