#!/usr/bin/env python3
"""Render truth-only diagnostics for a frozen single-electron release."""

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/jrb-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


FIGURE_NAMES = (
    "energy_deposition_closure",
    "local_quenching",
    "energy_response",
    "track_topology",
    "probe_population",
)


def _load(path):
    with np.load(path, allow_pickle=False) as payload:
        return {name: payload[name] for name in payload.files}


def _save(fig, output_dir, name):
    path = output_dir / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def _sample_indices(size, limit=50000):
    if size <= limit:
        return np.arange(size)
    return np.linspace(0, size - 1, limit, dtype=np.int64)


def _binned_median(x, y, bins):
    index = np.digitize(x, bins) - 1
    centers = 0.5 * (bins[:-1] + bins[1:])
    median = np.full(len(centers), np.nan)
    for bin_index in range(len(centers)):
        values = y[index == bin_index]
        if len(values):
            median[bin_index] = np.median(values)
    return centers, median


def build_figures(release_root: Path, output_dir: Path):
    """Build five diagnostics without opening waveform sample storage."""
    release_root = Path(release_root)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    truth = _load(release_root / "private/truth.npz")
    public = _load(release_root / "public/dev/truth.npz")
    config = json.loads(
        (release_root / "public/evaluation_config.json").read_text(encoding="utf-8")
    )

    offsets = np.asarray(truth["step_offsets"], dtype=np.int64)
    step_dep = np.asarray(truth["step_e_dep_mev"], dtype=float)
    step_vis = np.asarray(truth["step_e_vis_mev"], dtype=float)
    deposited = np.add.reduceat(step_dep, offsets[:-1])
    closure = deposited + truth["evt_e_escape_mev"] - truth["evt_total_energy"]
    max_closure = float(np.max(np.abs(closure)))
    if max_closure > 1e-8:
        raise ValueError(f"energy closure exceeds 1e-8 MeV: {max_closure:.3e}")

    visible_fraction = step_vis / np.maximum(step_dep, 1e-12)
    kinetic = np.asarray(truth["step_kinetic_mev"], dtype=float)
    low = kinetic < 0.05
    mid = (kinetic >= 0.5) & (kinetic <= 2.0)
    if not low.any() or not mid.any():
        raise ValueError("quenching diagnostic needs low and 0.5--2 MeV steps")
    low_mean = float(visible_fraction[low].mean())
    mid_mean = float(visible_fraction[mid].mean())
    if low_mean >= mid_mean:
        raise ValueError("low-energy visible-fraction suppression is absent")

    paths = {}
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].scatter(truth["evt_total_energy"], deposited, s=5, alpha=0.25)
    limit = float(np.max(truth["evt_total_energy"]))
    axes[0].plot([0, limit], [0, limit], "k--", lw=1, label="full deposition")
    axes[0].set(xlabel="total energy [MeV]", ylabel="deposited energy [MeV]")
    axes[0].legend()
    axes[1].hist(closure, bins=60)
    axes[1].set(
        xlabel="deposited + escaped - total [MeV]",
        ylabel="events",
        title=f"max |closure| = {max_closure:.2e} MeV",
    )
    paths["energy_deposition_closure"] = _save(
        fig, output_dir, "energy_deposition_closure"
    )

    chosen = _sample_indices(len(step_dep))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].scatter(kinetic[chosen], visible_fraction[chosen], s=2, alpha=0.12)
    positive = kinetic > 0
    bins = np.geomspace(max(1e-4, float(kinetic[positive].min())), float(kinetic.max()), 45)
    centers, median = _binned_median(kinetic, visible_fraction, bins)
    axes[0].plot(centers, median, color="black", lw=2)
    axes[0].set_xscale("log")
    axes[0].set(
        xlabel="local electron kinetic energy [MeV]",
        ylabel="visible / deposited energy",
        title=f"<50 keV {low_mean:.3f}; 0.5–2 MeV {mid_mean:.3f}",
    )
    dedx = np.asarray(truth["step_dedx_mev_cm"], dtype=float)
    axes[1].scatter(dedx[chosen], visible_fraction[chosen], s=2, alpha=0.12)
    axes[1].set(xlabel="local dE/dx [MeV/cm]", ylabel="visible / deposited energy")
    paths["local_quenching"] = _save(fig, output_dir, "local_quenching")

    role = np.asarray(public["evt_sample_role"])
    probe = role == 0
    probe_energy = np.asarray(public["evt_e_true"])[probe]
    response = np.asarray(public["evt_e_vis"])[probe] / probe_energy
    grid = np.unique(probe_energy)
    means = np.array([response[probe_energy == energy].mean() for energy in grid])
    errors = np.array([
        response[probe_energy == energy].std(ddof=1) / np.sqrt(np.count_nonzero(probe_energy == energy))
        if np.count_nonzero(probe_energy == energy) > 1 else 0.0
        for energy in grid
    ])
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.errorbar(grid, means, yerr=errors, marker="o", capsize=3)
    ax.set(
        xlabel="true electron energy [MeV]",
        ylabel="mean visible / true energy",
        title=f"Probe response; target R(1 MeV) ≤ {100 * config['energy_target_r_1mev']:.1f}%",
    )
    paths["energy_response"] = _save(fig, output_dir, "energy_response")

    step_count = np.diff(offsets)
    path_length = np.add.reduceat(np.asarray(truth["step_length_m"], dtype=float), offsets[:-1])
    event_energy = np.asarray(truth["evt_e_true"], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].scatter(event_energy, step_count, s=5, alpha=0.2)
    axes[0].set(xlabel="true energy [MeV]", ylabel="transport steps / event")
    axes[1].scatter(event_energy, 100 * path_length, s=5, alpha=0.2)
    axes[1].set(xlabel="true energy [MeV]", ylabel="summed step length [cm]")
    paths["track_topology"] = _save(fig, output_dir, "track_topology")

    radius = np.linalg.norm(np.asarray(public["evt_vertex_m"], dtype=float), axis=1)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    counts = np.array([np.count_nonzero(probe_energy == energy) for energy in grid])
    axes[0].bar(grid, counts, width=0.7)
    axes[0].set(xlabel="probe energy [MeV]", ylabel="events")
    axes[1].hist(radius[probe], bins=30, alpha=0.7, label="probe")
    axes[1].hist(radius[~probe], bins=30, alpha=0.5, label="control")
    axes[1].axvline(17.2, color="black", ls="--", lw=1, label="FV radius")
    axes[1].set(xlabel="vertex radius [m]", ylabel="events")
    axes[1].legend()
    paths["probe_population"] = _save(fig, output_dir, "probe_population")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, path in build_figures(args.release, args.output).items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
