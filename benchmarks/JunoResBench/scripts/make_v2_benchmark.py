#!/usr/bin/env python3
"""Assemble the public and evaluator-private JunoResBench v2 package."""

import argparse
import gc
from pathlib import Path
import shutil
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import (
    SparseSplitWriter,
    write_sparse_split,
)
from benchmarks.JunoResBench.scripts.generate_v2_dataset import (
    combine_populations,
    make_population,
    simulate_population,
    v2_detector_config,
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


def _copy_unless_same(source, target):
    if source.resolve() != Path(target).resolve():
        shutil.copyfile(source, target)


def _write_task_assets(task_directory):
    _copy_unless_same(
        BENCHMARK_ROOT / "task_v2" / "TASK.md", task_directory / "TASK.md"
    )
    _copy_unless_same(
        BENCHMARK_ROOT / "task_v2" / "submission_api.py",
        task_directory / "submission_api.py",
    )
    _copy_unless_same(
        BENCHMARK_ROOT / "task_v2" / "submission_worker.py",
        task_directory / "submission_worker.py",
    )
    _copy_unless_same(
        BENCHMARK_ROOT / "scripts" / "evaluate_v2.py",
        task_directory / "evaluate.py",
    )
    _copy_unless_same(
        BENCHMARK_ROOT / "baselines" / "v2_charge.py",
        task_directory / "baseline.py",
    )
    scoring = task_directory / "juno_res_bench"
    scoring.mkdir(exist_ok=True)
    (scoring / "__init__.py").write_text(
        "# Standalone scoring modules shipped with the public task.\n",
        encoding="utf-8",
    )
    for name in ("resolution.py", "sparse_waveforms.py"):
        _copy_unless_same(
            BENCHMARK_ROOT / "juno_res_bench" / name, scoring / name
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
    _require_fresh_output(root)
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


def _require_fresh_output(root):
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {root}")


def _simulate_to_split(path, population, seed, layout, simulator, truth_keys=None):
    """Simulate directly into one sparse split with bounded waveform memory."""
    writer = SparseSplitWriter(path)
    bundle = simulate_population(
        population,
        seed,
        layout=layout,
        simulator=simulator,
        observation_writer=writer,
    )
    truth = bundle["truth"]
    if truth_keys == ():
        truth = None
    elif truth_keys is not None:
        truth = {key: truth[key] for key in truth_keys}
    writer.finalize(_public_metadata(bundle["metadata"]), truth=truth)
    return bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="package parent directory")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--n-pmt", type=int, default=17612)
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--dev-probe-events-per-point", type=int, default=1000)
    parser.add_argument("--dev-controls", type=int, default=6400)
    parser.add_argument("--final-probe-events-per-point", type=int, default=10000)
    parser.add_argument("--final-controls", type=int, default=6400)
    args = parser.parse_args()
    root = Path(args.out)
    _require_fresh_output(root)

    streams = np.random.SeedSequence(args.seed).spawn(8)
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

    config = v2_detector_config()
    layout = PMTLayout.uniform(args.n_pmt, config.detector_radius_m)
    simulator = DetectorSim(config, layout, seed=seeds[7])
    task = root / "task_v2"
    private = root / "blind_truth_v2"
    task.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        task / "detector_geometry.npz", pmt_positions_m=layout.positions_m
    )

    calibration = _simulate_to_split(
        task / "calibration",
        calibration_population,
        seeds[7],
        layout,
        simulator,
        truth_keys=(),
    )
    np.savez_compressed(
        task / "calibration" / "labels.npz", **calibration["labels"]
    )
    del calibration
    gc.collect()

    dev = _simulate_to_split(
        task / "dev",
        dev_population,
        seeds[7],
        layout,
        simulator,
        truth_keys=DEV_TRUTH_KEYS,
    )
    del dev
    gc.collect()

    final = _simulate_to_split(
        private / "final_observations",
        final_population,
        seeds[7],
        layout,
        simulator,
        truth_keys=None,
    )
    (private / "final_observations" / "truth.npz").replace(
        private / "truth.npz"
    )
    del final
    gc.collect()
    _write_task_assets(task)


if __name__ == "__main__":
    main()
