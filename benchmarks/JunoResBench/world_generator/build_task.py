#!/usr/bin/env python3
"""Create data-only public/private datasets from the private world."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
    PMTLayout,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.sparse_waveforms import SparseSplitWriter, encode_event
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import PARTICLE_CODE_TYPE
from benchmarks.JunoResBench.world_generator.populations import calibration_population, physics_population
from benchmarks.JunoResBench.world_generator.oracle_vertex import charge_pattern_vertex_rms, freeze_threshold


PUBLIC_METADATA = {"layout", "n_pmt", "radius_m", "sample_interval_ns", "adc_bits", "window_ns"}


def roi_threshold_adc(wave_cfg, sigma=5.0):
    """Return a noise-scaled integer threshold for sparse waveform storage."""
    return int(np.ceil(float(sigma) * wave_cfg.noise_sigma_mv * 1e-3 / wave_cfg.lsb_v))


def _metadata(config, layout, simulator):
    return {
        "seed": None,
        "detector_config": asdict(config),
        "layout": "uniform",
        "n_pmt": layout.n_pmt,
        "radius_m": layout.radius_m,
        "sample_interval_ns": simulator.wave_cfg.sample_interval_ns,
        "adc_bits": simulator.wave_cfg.adc_bits,
        "window_ns": config.window_ns,
    }


def _simulate(population, simulator, layout, destination, public_truth):
    writer = SparseSplitWriter(destination)
    truth_rows = {key: [] for key in ("evt_e_vis", "evt_e_dep_mev", "evt_e_escape_mev", "evt_total_energy")}
    step_rows = {key: [] for key in ("step_pos_m", "step_e_dep_mev", "step_e_vis_mev", "step_dedx_mev_cm", "step_kinetic_mev", "step_length_m", "step_kind")}
    offsets = [0]
    for index, energy in enumerate(population["evt_e_true"]):
        event = simulator.generate(
            *population["evt_vertex_m"][index], float(energy),
            t0_ns=float(population["evt_t0_ns"][index]),
            direction=tuple(population["evt_direction"][index]),
            particle_type=PARTICLE_CODE_TYPE[int(population["evt_particle_type"][index])],
        )
        adc = np.asarray(event.adc, dtype=np.uint16)
        if adc.size == 0:
            adc = np.empty((0, simulator.wave_cfg.n_samples), dtype=np.uint16)
        writer.append(encode_event(
            adc,
            event.adc_ids,
            simulator.wave_cfg.baseline_adc,
            roi_threshold_adc(simulator.wave_cfg),
            16,
            48,
        ))
        truth_rows["evt_e_vis"].append(event.e_vis_mev)
        truth_rows["evt_e_dep_mev"].append(event.e_dep_mev)
        truth_rows["evt_e_escape_mev"].append(event.e_escape_mev)
        truth_rows["evt_total_energy"].append(float(energy) + (1.021998 if population["evt_particle_type"][index] == 2 else 0.0))
        for key in step_rows:
            step_rows[key].append(getattr(event, key))
        offsets.append(offsets[-1] + len(event.step_e_dep_mev))
    truth = {key: np.asarray(value) for key, value in population.items()}
    truth.update({key: np.asarray(value) for key, value in truth_rows.items()})
    truth["step_offsets"] = np.asarray(offsets, dtype=np.int64)
    for key, blocks in step_rows.items():
        truth[key] = np.concatenate(blocks) if blocks else np.empty(0)
    metadata = _metadata(simulator.cfg, layout, simulator)
    writer.finalize({key: metadata[key] for key in PUBLIC_METADATA}, truth=public_truth(truth) if public_truth else None)
    return truth


def _ensure_fresh(path):
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {path}")


def select_layout(mode, n_pmt, position_csv, type_csv):
    """Select real production geometry or an explicit synthetic test layout."""
    if mode == "uniform":
        if n_pmt is None:
            raise ValueError("uniform geometry requires n_pmt")
        return PMTLayout.uniform(n_pmt, DetectorConfig().detector_radius_m)
    if mode == "juno":
        return PMTLayout.from_juno_csv(position_csv, type_csv)
    raise ValueError(f"unknown geometry mode: {mode}")


def build(task_name, output_root, seed, layout, calibration_events_per_point, probe_events_per_point, controls):
    """Generate a task's data artifacts without copying executable code."""
    output_root = Path(output_root)
    _ensure_fresh(output_root)
    streams = np.random.SeedSequence(seed).spawn(5)
    seeds = [int(stream.generate_state(1, dtype=np.uint64)[0]) for stream in streams]
    config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
    simulator = DetectorSim(config, layout, seed=seeds[4])
    public = output_root / "public"
    private = output_root / "private"
    public.mkdir(parents=True)
    private.mkdir()
    np.savez_compressed(public / "detector_geometry.npz", pmt_positions_m=layout.positions_m)
    calibration = calibration_population(seeds[0], calibration_events_per_point)
    _simulate(calibration, simulator, layout, public / "calibration", None)
    np.savez_compressed(public / "calibration" / "labels.npz", source_energy_mev=calibration["evt_e_true"], deployment_position_m=calibration["evt_vertex_m"])
    dev = physics_population(task_name, seeds[1], probe_events_per_point, controls)
    public_keys = {"evt_sample_role", "evt_e_true", "evt_e_vis"}
    if task_name == "electron_single_site":
        public_keys.add("evt_vertex_m")
    _simulate(dev, simulator, layout, public / "dev", lambda truth: {key: truth[key] for key in public_keys})
    final = physics_population(task_name, seeds[2], probe_events_per_point, controls)
    truth = _simulate(final, simulator, layout, private / "final_observations", None)
    np.savez_compressed(private / "truth.npz", **truth)
    evaluation = {"energy_target_r_1mev": 0.03}
    if task_name == "electron_single_site":
        vertices = final["evt_vertex_m"][(final["evt_sample_role"] == 0) & (final["evt_e_true"] == 1.0)]
        oracle_rms = charge_pattern_vertex_rms(vertices, layout, config)
        threshold = freeze_threshold(oracle_rms)
        (private / "electron_oracle.json").write_text(json.dumps({"method": "ideal_charge_pattern_fisher", "oracle_vertex_rms_m": oracle_rms, "vertex_threshold_m": threshold}, indent=2) + "\n", encoding="utf-8")
        evaluation["vertex_threshold_m"] = threshold
    (public / "evaluation_config.json").write_text(json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--geometry-mode", choices=("juno", "uniform"), default="juno")
    parser.add_argument("--juno-position-csv", default=JUNO_LPMT_CSV)
    parser.add_argument("--juno-type-csv", default=JUNO_LPMT_TYPE_CSV)
    parser.add_argument("--n-pmt", type=int)
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--probe-events-per-point", type=int, default=1000)
    parser.add_argument("--controls", type=int, default=6400)
    args = parser.parse_args()
    layout = select_layout(
        args.geometry_mode,
        args.n_pmt,
        args.juno_position_csv,
        args.juno_type_csv,
    )
    build(args.task, args.out, args.seed, layout, args.calibration_events_per_point, args.probe_events_per_point, args.controls)


if __name__ == "__main__":
    main()
