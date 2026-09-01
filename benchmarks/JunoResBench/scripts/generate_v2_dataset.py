#!/usr/bin/env python3
"""Generate calibration or IBD-like positron data for JunoResBench v2."""

import argparse
from dataclasses import asdict
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.resolution import PROBE_KINETIC_MEV
from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import (
    encode_event,
    write_sparse_split,
)
from benchmarks.JunoResBench.juno_res_bench.truth import (
    PARTICLE_CODE_TYPE,
)


CALIBRATION_ENERGIES_MEV = np.array([0.511, 1.022, 2.223, 4.44, 8.0])


def _calibration_positions():
    positions = [np.zeros(3)]
    for radius in (8.0, 14.0):
        for axis in range(3):
            for sign in (-1.0, 1.0):
                position = np.zeros(3)
                position[axis] = sign * radius
                positions.append(position)
    return np.asarray(positions)


def _sample_vertices(rng, n, radius):
    r = float(radius) * rng.random(n) ** (1.0 / 3.0)
    cos_theta = rng.uniform(-1.0, 1.0, n)
    sin_theta = np.sqrt(1.0 - cos_theta**2)
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    return np.column_stack(
        (r * sin_theta * np.cos(phi), r * sin_theta * np.sin(phi), r * cos_theta)
    )


def _sample_directions(rng, n):
    cos_theta = rng.uniform(-1.0, 1.0, n)
    sin_theta = np.sqrt(1.0 - cos_theta**2)
    phi = rng.uniform(0.0, 2.0 * np.pi, n)
    return np.column_stack(
        (sin_theta * np.cos(phi), sin_theta * np.sin(phi), cos_theta)
    )


def make_population(
    mode,
    seed,
    events_per_point=20,
    events=12800,
    fiducial_radius_m=16.0,
):
    """Construct deterministic inputs without running detector simulation."""
    rng = np.random.default_rng(seed)
    if mode == "calibration":
        positions = _calibration_positions()
        energy = np.repeat(CALIBRATION_ENERGIES_MEV, len(positions))
        vertex = np.tile(positions, (len(CALIBRATION_ENERGIES_MEV), 1))
        energy = np.repeat(energy, events_per_point)
        vertex = np.repeat(vertex, events_per_point, axis=0)
        particle_type = np.full(len(energy), 1, dtype=np.int8)
        role = np.full(len(energy), -1, dtype=np.int8)
    elif mode == "probes":
        energy = np.repeat(PROBE_KINETIC_MEV, events_per_point)
        vertex = _sample_vertices(rng, len(energy), fiducial_radius_m)
        particle_type = np.full(len(energy), 2, dtype=np.int8)
        role = np.zeros(len(energy), dtype=np.int8)
    elif mode == "controls":
        strata = np.arange(int(events)) % 64
        energy = 11.0 * (strata + rng.random(int(events))) / 64.0
        energy = energy[rng.permutation(int(events))]
        vertex = _sample_vertices(rng, len(energy), fiducial_radius_m)
        particle_type = np.full(len(energy), 2, dtype=np.int8)
        role = np.ones(len(energy), dtype=np.int8)
    else:
        raise ValueError("mode must be calibration, probes, or controls")

    return {
        "evt_e_true": energy.astype(float),
        "evt_vertex_m": vertex.astype(float),
        "evt_direction": _sample_directions(rng, len(energy)),
        "evt_t0_ns": rng.uniform(0.0, 1000.0, len(energy)),
        "evt_particle_type": particle_type,
        "evt_sample_role": role,
    }


def combine_populations(*populations, seed):
    """Combine compatible populations and hide role/energy ordering."""
    if not populations:
        raise ValueError("at least one population is required")
    keys = set(populations[0])
    if any(set(population) != keys for population in populations[1:]):
        raise ValueError("population schemas do not match")
    combined = {
        key: np.concatenate([population[key] for population in populations])
        for key in keys
    }
    order = np.random.default_rng(seed).permutation(len(combined["evt_e_true"]))
    return {key: value[order] for key, value in combined.items()}


def simulate_population(
    population,
    seed,
    layout=None,
    simulator=None,
    fiducial_radius_m=16.0,
    threshold_adc=6,
    pre_samples=16,
    post_samples=48,
):
    """Run the authoritative trace/full-readout world for one population."""
    cfg = DetectorConfig(optics_mode="trace", full_readout=True)
    detector_layout = layout or PMTLayout.uniform(
        17612, radius_m=cfg.detector_radius_m
    )
    simulator = simulator or DetectorSim(cfg, detector_layout, seed=seed)
    if simulator.layout is not detector_layout:
        raise ValueError("simulator and requested layout must be identical")
    observations = []
    truth_rows = {
        "evt_e_vis": [],
        "evt_e_dep": [],
        "evt_e_escape": [],
        "evt_total_energy": [],
    }
    step_offsets = [0]
    step_rows = {
        "step_pos_m": [],
        "step_e_dep_mev": [],
        "step_e_vis_mev": [],
        "step_dedx_mev_cm": [],
        "step_kind": [],
    }

    for index, energy in enumerate(population["evt_e_true"]):
        vertex = population["evt_vertex_m"][index]
        event = simulator.generate(
            *vertex,
            float(energy),
            t0_ns=float(population["evt_t0_ns"][index]),
            direction=tuple(population["evt_direction"][index]),
            particle_type=PARTICLE_CODE_TYPE[
                int(population["evt_particle_type"][index])
            ],
        )
        adc = np.asarray(event.adc, dtype=np.uint16)
        if adc.size == 0:
            adc = np.empty((0, simulator.wave_cfg.n_samples), dtype=np.uint16)
        observations.append(encode_event(
            adc,
            event.adc_ids,
            simulator.wave_cfg.baseline_adc,
            threshold_adc,
            pre_samples,
            post_samples,
        ))
        truth_rows["evt_e_vis"].append(event.e_vis_mev)
        truth_rows["evt_e_dep"].append(event.e_dep_mev)
        truth_rows["evt_e_escape"].append(event.e_escape_mev)
        total = float(energy) + (
            1.021998 if int(population["evt_particle_type"][index]) == 2 else 0.0
        )
        truth_rows["evt_total_energy"].append(total)
        for key, values in (
            ("step_pos_m", event.step_pos_m),
            ("step_e_dep_mev", event.step_e_dep_mev),
            ("step_e_vis_mev", event.step_e_vis_mev),
            ("step_dedx_mev_cm", event.step_dedx_mev_cm),
            ("step_kind", event.step_kind),
        ):
            step_rows[key].append(values)
        step_offsets.append(step_offsets[-1] + len(event.step_e_dep_mev))

    truth = {key: np.asarray(value) for key, value in population.items()}
    truth.update({key: np.asarray(value) for key, value in truth_rows.items()})
    truth["step_offsets"] = np.asarray(step_offsets, dtype=np.int64)
    for key, blocks in step_rows.items():
        truth[key] = np.concatenate(blocks) if blocks else np.empty(0)
    truth["fiducial_radius_m"] = np.asarray([fiducial_radius_m], dtype=float)

    metadata = {
        "seed": int(seed),
        "detector_config": asdict(cfg),
        "layout": "provided" if layout is not None else "uniform",
        "n_pmt": detector_layout.n_pmt,
        "radius_m": detector_layout.radius_m,
        "sample_interval_ns": simulator.wave_cfg.sample_interval_ns,
        "adc_bits": simulator.wave_cfg.adc_bits,
        "window_ns": cfg.window_ns,
    }
    labels = {
        "source_energy_mev": truth["evt_e_true"],
        "deployment_position_m": truth["evt_vertex_m"],
    }
    return {
        "observations": observations,
        "truth": truth,
        "labels": labels,
        "metadata": metadata,
        "detector_geometry": detector_layout.positions_m,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["calibration", "probes", "controls"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--events-per-point", type=int, default=20)
    parser.add_argument("--events", type=int, default=12800)
    parser.add_argument("--fiducial-radius-m", type=float, default=16.0)
    parser.add_argument("--n-pmt", type=int, default=17612)
    args = parser.parse_args()

    population = make_population(
        args.mode,
        args.seed,
        events_per_point=args.events_per_point,
        events=args.events,
        fiducial_radius_m=args.fiducial_radius_m,
    )
    layout = PMTLayout.uniform(args.n_pmt)
    bundle = simulate_population(
        population,
        seed=args.seed + 1,
        layout=layout,
        fiducial_radius_m=args.fiducial_radius_m,
    )
    write_sparse_split(
        args.out,
        bundle["metadata"],
        bundle["observations"],
        truth=bundle["truth"],
    )
    if args.mode == "calibration":
        np.savez_compressed(Path(args.out) / "labels.npz", **bundle["labels"])
    np.savez_compressed(
        Path(args.out) / "detector_geometry.npz",
        pmt_positions_m=bundle["detector_geometry"],
    )


if __name__ == "__main__":
    main()
