#!/usr/bin/env python3
"""Plot a bounded, owner-side audit of a frozen electron waveform release."""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/jrb-matplotlib")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
import numpy as np  # noqa: E402


FIGURE_NAMES = (
    "vertex_distribution",
    "energy_radius_coverage",
    "radial_light_yield",
    "hit_pattern_comparison",
    "charge_pattern_comparison",
    "hit_multiplicity_vs_energy",
    "charge_vs_energy",
    "event_anatomy",
    "first_hit_time",
    "time_vs_distance",
    "tof_corrected_residual",
    "timing_vs_radius",
    "waveform_examples",
    "waveform_overlays",
    "pulse_integral_vs_peak",
    "roi_structure",
)


@dataclass(frozen=True)
class EventWaveforms:
    segment_pmt_ids: np.ndarray
    segment_start_samples: np.ndarray
    segment_sample_offsets: np.ndarray
    samples: np.ndarray
    n_samples: int
    pre_samples: int


@dataclass(frozen=True)
class EventMetrics:
    index: int
    pmt_ids: np.ndarray
    charge: np.ndarray
    peak: np.ndarray
    first_sample: np.ndarray
    signal_mask: np.ndarray
    roi_starts: np.ndarray
    roi_lengths: np.ndarray
    event: EventWaveforms


class ReleaseWaveforms:
    """Event-local reader for one sparse split backed by a sample memmap."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.metadata = json.loads(
            (self.path / "metadata.json").read_text(encoding="utf-8")
        )
        with np.load(self.path / "index.npz", allow_pickle=False) as index:
            self.event_offsets = index["event_segment_offsets"]
            self.sample_offsets = index["segment_sample_offsets"]
            self.segment_pmt_ids = index["segment_pmt_ids"]
            self.segment_starts = index["segment_start_samples"]
        self.samples = np.load(
            self.path / "segment_samples.npy", mmap_mode="r", allow_pickle=False
        )
        n_segment = len(self.segment_pmt_ids)
        if (
            self.event_offsets.ndim != 1
            or len(self.event_offsets) < 2
            or int(self.event_offsets[0]) != 0
            or int(self.event_offsets[-1]) != n_segment
        ):
            raise ValueError("invalid event offsets")
        if (
            self.sample_offsets.shape != (n_segment + 1,)
            or int(self.sample_offsets[0]) != 0
            or int(self.sample_offsets[-1]) != len(self.samples)
        ):
            raise ValueError("invalid sample offsets")
        if self.segment_starts.shape != (n_segment,):
            raise ValueError("segment arrays do not align")

    def __len__(self):
        return len(self.event_offsets) - 1

    def read_event(self, index: int) -> EventWaveforms:
        if index < 0 or index >= len(self):
            raise IndexError(index)
        lo = int(self.event_offsets[index])
        hi = int(self.event_offsets[index + 1])
        offsets = self.sample_offsets[lo : hi + 1]
        if len(offsets) == 0:
            offsets = np.zeros(1, dtype=np.int64)
        sample_lo, sample_hi = int(offsets[0]), int(offsets[-1])
        if sample_lo > sample_hi or np.any(offsets[1:] < offsets[:-1]):
            raise ValueError(f"invalid sample offsets in event {index}")
        return EventWaveforms(
            segment_pmt_ids=self.segment_pmt_ids[lo:hi],
            segment_start_samples=self.segment_starts[lo:hi],
            segment_sample_offsets=offsets - sample_lo,
            samples=self.samples[sample_lo:sample_hi],
            n_samples=int(self.metadata["n_samples"]),
            pre_samples=int(self.metadata["pre_samples"]),
        )


def _load_npz(path: Path):
    with np.load(path, allow_pickle=False) as data:
        return {name: data[name] for name in data.files}


def _event_metrics(reader: ReleaseWaveforms, index: int) -> EventMetrics:
    event = reader.read_event(index)
    ids = event.segment_pmt_ids.astype(np.int64, copy=False)
    lengths = np.diff(event.segment_sample_offsets).astype(np.int64)
    if len(ids) == 0:
        empty = np.empty(0, dtype=float)
        return EventMetrics(
            index, ids, empty, empty, empty, np.zeros(0, dtype=bool), ids, ids, event
        )
    unique, inverse = np.unique(ids, return_inverse=True)
    charge = np.zeros(len(unique), dtype=float)
    peak = np.zeros(len(unique), dtype=float)
    first = np.full(len(unique), np.inf)
    segment_charge = np.empty(len(ids), dtype=float)
    segment_peak = np.empty(len(ids), dtype=float)
    segment_first = np.full(len(ids), np.inf)
    pulse_threshold = 5.0 * float(reader.metadata["threshold_adc"])
    for segment, (lo, hi) in enumerate(zip(
        event.segment_sample_offsets[:-1], event.segment_sample_offsets[1:]
    )):
        residual = np.asarray(event.samples[int(lo):int(hi)], float)
        signal = -residual
        segment_charge[segment] = signal.sum()
        segment_peak[segment] = signal.max(initial=0)
        crossing = np.flatnonzero(signal >= pulse_threshold)
        if len(crossing):
            segment_first[segment] = (
                float(event.segment_start_samples[segment]) + float(crossing[0])
            )
    np.add.at(charge, inverse, segment_charge)
    np.maximum.at(peak, inverse, segment_peak)
    np.minimum.at(
        first,
        inverse,
        segment_first,
    )
    return EventMetrics(
        index=index,
        pmt_ids=unique,
        charge=charge,
        peak=peak,
        first_sample=first,
        signal_mask=np.isfinite(first),
        roi_starts=event.segment_start_samples.astype(np.int64),
        roi_lengths=lengths,
        event=event,
    )


def _waveform(metric: EventMetrics, pmt_id: int):
    signal = np.zeros(metric.event.n_samples, dtype=float)
    event = metric.event
    for segment in np.flatnonzero(event.segment_pmt_ids == pmt_id):
        lo = int(event.segment_sample_offsets[segment])
        hi = int(event.segment_sample_offsets[segment + 1])
        start = int(event.segment_start_samples[segment])
        stop = start + hi - lo
        signal[start:stop] = np.maximum(-np.asarray(event.samples[lo:hi], float), 0)
    return signal


def _selection(energy, radius, limit, anchors):
    if limit < 2:
        raise ValueError("sample_limit must be at least 2")
    candidates = np.linspace(0, len(energy) - 1, min(limit, len(energy)), dtype=int)
    chosen = []
    for index in anchors + candidates.tolist():
        if index not in chosen:
            chosen.append(index)
        if len(chosen) == min(limit, len(energy)):
            break
    return np.asarray(chosen, dtype=int)


def _sky(positions):
    radius = np.linalg.norm(positions, axis=1)
    return (
        np.arctan2(positions[:, 1], positions[:, 0]),
        np.arcsin(np.clip(positions[:, 2] / radius, -1, 1)),
    )


def _save(fig, output: Path, name: str):
    path = output / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _pattern_figure(metrics, positions, values, label, title):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), subplot_kw={"projection": "mollweide"})
    for ax, metric, subtitle in zip(axes, metrics, ("center-like", "edge-like")):
        selected_ids = metric.pmt_ids[metric.signal_mask]
        lon, lat = _sky(positions[selected_ids])
        color = values(metric)[metric.signal_mask]
        if len(color) == 0:
            ax.text(0.5, 0.5, "no selected pulse", transform=ax.transAxes, ha="center")
        elif np.allclose(color, color[0]):
            ax.scatter(lon, lat, s=5, color="tab:blue")
        else:
            kwargs = {"c": color, "s": 5, "cmap": "viridis"}
            positive = color[color > 0]
            if len(positive) and positive.max() / positive.min() > 20:
                kwargs["norm"] = LogNorm(
                    vmin=max(positive.min(), 1), vmax=positive.max()
                )
            artist = ax.scatter(lon, lat, **kwargs)
            fig.colorbar(artist, ax=ax, shrink=0.65, label=label)
        ax.grid(alpha=0.25)
        ax.set_title(f"{subtitle}; event {metric.index}")
    fig.suptitle(title)
    return fig


def build_waveform_figures(release_root: Path, output_dir: Path, sample_limit=32):
    release_root, output = Path(release_root), Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    reader = ReleaseWaveforms(release_root / "public/dev")
    public = _load_npz(release_root / "public/dev/truth.npz")
    private = _load_npz(release_root / "private/truth.npz")
    geometry = _load_npz(release_root / "public/detector_geometry.npz")
    positions = np.asarray(geometry["pmt_positions_m"], float)
    energy = np.asarray(public["evt_e_true"], float)
    vertices = np.asarray(public["evt_vertex_m"], float)
    radius = np.linalg.norm(vertices, axis=1)
    if len(reader) != len(energy) or len(private["evt_t0_ns"]) != len(energy):
        raise ValueError("waveform and truth event counts differ")
    role = np.asarray(public["evt_sample_role"])
    probe_five = np.flatnonzero((role == 0) & np.isclose(energy, 5.0))
    if len(probe_five) < 2:
        probe_five = np.flatnonzero(np.isclose(energy, energy[np.argmin(abs(energy - 5.0))]))
    anchors = [
        int(probe_five[np.argmin(radius[probe_five])]),
        int(probe_five[np.argmax(radius[probe_five])]),
    ]
    selected = _selection(energy, radius, int(sample_limit), anchors)
    metrics = [_event_metrics(reader, int(index)) for index in selected]
    center, edge = metrics[0], metrics[1]
    hit_count = np.asarray([np.count_nonzero(item.signal_mask) for item in metrics])
    total_charge = np.asarray([item.charge.sum() for item in metrics])
    median_time = np.asarray([
        np.median(item.first_sample[item.signal_mask]) if item.signal_mask.any() else np.nan
        for item in metrics
    ])
    sample_energy = energy[selected]
    sample_radius = radius[selected]
    paths = {}

    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, (a, b, xlabel, ylabel) in zip(axes, (
        (vertices[:, 0], vertices[:, 1], "x [m]", "y [m]"),
        (vertices[:, 0], vertices[:, 2], "x [m]", "z [m]"),
        (vertices[:, 1], vertices[:, 2], "y [m]", "z [m]"),
    )):
        ax.hexbin(a, b, gridsize=35, mincnt=1)
        ax.set(xlabel=xlabel, ylabel=ylabel, aspect="equal")
    fig.suptitle("Vertex population: spherical-volume deployment should fill all projections")
    paths["vertex_distribution"] = _save(fig, output, "vertex_distribution")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    scatter = ax.scatter(radius, energy, c=np.asarray(public["evt_sample_role"]), s=5, alpha=0.4)
    ax.set(xlabel="vertex radius [m]", ylabel="true energy [MeV]",
           title="Energy-radius coverage: probe and control samples span the fiducial volume")
    fig.colorbar(scatter, ax=ax, label="sample role")
    paths["energy_radius_coverage"] = _save(fig, output, "energy_radius_coverage")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(sample_radius, total_charge / sample_energy, c=sample_energy, s=28)
    ax.set(xlabel="vertex radius [m]", ylabel="stored pulse integral / MeV [ADC count]",
           title="Radial light yield: geometry-driven nonuniformity must be calibratable")
    paths["radial_light_yield"] = _save(fig, output, "radial_light_yield")

    fig = _pattern_figure(
        (center, edge), positions, lambda item: np.ones(len(item.pmt_ids)),
        "stored-hit PMT", "Hit pattern: edge event should be localized toward nearby PMTs",
    )
    paths["hit_pattern_comparison"] = _save(fig, output, "hit_pattern_comparison")

    fig = _pattern_figure(
        (center, edge), positions, lambda item: item.charge,
        "pulse integral [ADC count]", "Charge pattern: off-center illumination should be asymmetric",
    )
    paths["charge_pattern_comparison"] = _save(fig, output, "charge_pattern_comparison")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(sample_energy, hit_count, c=sample_radius, s=30)
    ax.set(xlabel="true energy [MeV]", ylabel="unique stored PMTs",
           title="Hit multiplicity: occupancy should rise with energy and vary with radius")
    paths["hit_multiplicity_vs_energy"] = _save(fig, output, "hit_multiplicity_vs_energy")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(sample_energy, total_charge, c=sample_radius, s=30, label="sampled events")
    if len(sample_energy) >= 2:
        slope, intercept = np.polyfit(sample_energy, total_charge, 1)
        grid = np.linspace(sample_energy.min(), sample_energy.max(), 100)
        ax.plot(grid, slope * grid + intercept, "k--", label="linear reference")
    ax.set(xlabel="true energy [MeV]", ylabel="stored pulse integral [ADC count]",
           title="Charge response: near-linearity with position-dependent spread")
    ax.legend()
    paths["charge_vs_energy"] = _save(fig, output, "charge_vs_energy")

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(2, 2, 1, projection="mollweide")
    edge_ids = edge.pmt_ids[edge.signal_mask]
    edge_charge = edge.charge[edge.signal_mask]
    lon, lat = _sky(positions[edge_ids])
    sc = ax.scatter(lon, lat, c=edge_charge, s=5, cmap="viridis", norm=LogNorm())
    fig.colorbar(sc, ax=ax, shrink=0.6, label="integral")
    ax.set_title("charge sky map")
    ax = fig.add_subplot(2, 2, 2)
    ax.hist(edge.first_sample[edge.signal_mask], bins=50)
    ax.set(xlabel="first stored pulse sample", ylabel="PMTs", title="first-hit timing")
    ax = fig.add_subplot(2, 2, 3)
    ax.scatter(edge_charge, edge.peak[edge.signal_mask], s=8)
    ax.set(xlabel="integral", ylabel="peak", title="channel pulse shape")
    ax = fig.add_subplot(2, 2, 4)
    bright = int(edge_ids[np.argmax(edge_charge)])
    ax.plot(_waveform(edge, bright), lw=0.8)
    ax.set(xlabel="sample [ns]", ylabel="baseline - ADC", title=f"brightest PMT {bright}")
    fig.suptitle(f"One-event anatomy: event {edge.index}, E={energy[edge.index]:.2f} MeV, r={radius[edge.index]:.2f} m")
    paths["event_anatomy"] = _save(fig, output, "event_anatomy")

    all_first = np.concatenate([
        item.first_sample[item.signal_mask] for item in metrics if item.signal_mask.any()
    ])
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(all_first, bins=100, log=True)
    ax.set(xlabel="first stored pulse sample [ns]", ylabel="PMTs",
           title="First-hit time: prompt peak with late-light/dark tail expected")
    paths["first_hit_time"] = _save(fig, output, "first_hit_time")

    distance_blocks, time_blocks, residual_blocks = [], [], []
    for item in metrics:
        ids = item.pmt_ids[item.signal_mask]
        distance = np.linalg.norm(positions[ids] - vertices[item.index], axis=1)
        raw = item.first_sample[item.signal_mask]
        residual = raw - distance * 1.50 / 0.299792458
        residual -= np.median(residual)
        distance_blocks.append(distance)
        time_blocks.append(raw)
        residual_blocks.append(residual)
    all_distance = np.concatenate(distance_blocks)
    all_time = np.concatenate(time_blocks)
    all_residual = np.concatenate(residual_blocks)
    thin = np.linspace(0, len(all_time) - 1, min(50000, len(all_time)), dtype=int)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(all_distance[thin], all_time[thin], s=2, alpha=0.15)
    ax.set(xlabel="vertex-PMT distance [m]", ylabel="first stored pulse sample [ns]",
           title="Time-distance relation: longer optical paths should arrive later")
    paths["time_vs_distance"] = _save(fig, output, "time_vs_distance")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(all_residual, bins=120, range=(-100, 250), log=True)
    ax.axvline(0, color="k", ls="--")
    ax.set(xlabel="per-event-centered t - 1.50 d/c [ns]", ylabel="PMTs",
           title="TOF residual: prompt core plus scattering/re-emission tail")
    paths["tof_corrected_residual"] = _save(fig, output, "tof_corrected_residual")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(sample_radius, median_time, c=sample_energy, s=30)
    ax.set(xlabel="vertex radius [m]", ylabel="median first pulse sample [ns]",
           title="Trigger-relative timing versus radius")
    paths["timing_vs_radius"] = _save(fig, output, "timing_vs_radius")

    channels = []
    for item in metrics:
        channels.extend(
            (float(q), item, int(pmt))
            for q, pmt in zip(item.charge[item.signal_mask], item.pmt_ids[item.signal_mask])
        )
    channels.sort(key=lambda row: row[0])
    chosen_channels = [channels[int(fraction * (len(channels) - 1))] for fraction in (0.1, 0.5, 0.95)]
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    for ax, (charge, item, pmt), label in zip(axes, chosen_channels, ("low", "median", "high")):
        ax.plot(_waveform(item, pmt), lw=0.8)
        ax.set_ylabel("baseline-ADC")
        ax.set_title(f"{label} integral: event {item.index}, PMT {pmt}, Q={charge:.0f}")
    axes[-1].set_xlabel("sample [ns]")
    fig.suptitle("Representative stored waveforms: shaped negative ADC pulses shown positive")
    paths["waveform_examples"] = _save(fig, output, "waveform_examples")

    fig, ax = plt.subplots(figsize=(8, 4.8))
    for charge, item, pmt in channels[::max(1, len(channels) // 24)][:24]:
        wave = _waveform(item, pmt)
        peak_at = int(np.argmax(wave))
        lo, hi = max(0, peak_at - 30), min(len(wave), peak_at + 80)
        segment = wave[lo:hi]
        ax.plot(np.arange(len(segment)) - min(30, peak_at), segment / max(segment.max(), 1), alpha=0.3)
    ax.set(xlabel="sample relative to pulse peak [ns]", ylabel="normalized amplitude",
           title="Pulse-shape overlays: common shaping with noise/overlap variation")
    paths["waveform_overlays"] = _save(fig, output, "waveform_overlays")

    all_charge = np.concatenate([item.charge[item.signal_mask] for item in metrics])
    all_peak = np.concatenate([item.peak[item.signal_mask] for item in metrics])
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(all_peak, all_charge, s=3, alpha=0.2)
    ax.set(xlabel="peak amplitude [ADC count]", ylabel="pulse integral [ADC count]",
           title="Integral versus peak: linear core; pile-up broadens high charge")
    paths["pulse_integral_vs_peak"] = _save(fig, output, "pulse_integral_vs_peak")

    all_starts = np.concatenate([item.roi_starts for item in metrics])
    all_lengths = np.concatenate([item.roi_lengths for item in metrics])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    axes[0].hist(all_starts, bins=100, log=True)
    axes[0].set(xlabel="ROI start sample", ylabel="segments", title="ROI timing across readout window")
    axes[1].hist(all_lengths, bins=min(80, max(10, len(np.unique(all_lengths)))), log=True)
    axes[1].axvline(reader.metadata["pre_samples"] + reader.metadata["post_samples"] + 1,
                    color="k", ls="--", label="isolated threshold ROI")
    axes[1].set(xlabel="ROI length [samples]", ylabel="segments",
                title="ROI length: long tail reveals merged/overlapping pulses")
    axes[1].legend()
    paths["roi_structure"] = _save(fig, output, "roi_structure")

    summary = {
        "events_total": len(reader),
        "events_scanned": len(metrics),
        "selected_event_indices": selected.tolist(),
        "center_event": center.index,
        "edge_event": edge.index,
        "waveform_samples_total": int(len(reader.samples)),
        "waveform_samples_read": int(sum(item.event.samples.size for item in metrics)),
        "mean_hit_pmts": float(hit_count.mean()),
        "mean_stored_pmts": float(np.mean([len(item.pmt_ids) for item in metrics])),
        "mean_integral_per_mev": float(np.mean(total_charge / sample_energy)),
        "median_first_sample_ns": float(np.nanmedian(median_time)),
        "pulse_selection_threshold_adc": 5.0 * float(reader.metadata["threshold_adc"]),
        "raw_roi_threshold_adc": float(reader.metadata["threshold_adc"]),
        "raw_roi_start_zero_fraction": float(np.mean(all_starts == 0)),
        "raw_roi_near_full_window_fraction": float(
            np.mean(all_lengths >= 0.9 * reader.metadata["n_samples"])
        ),
        "tof_residual_core_sigma_ns": float(np.std(all_residual[np.abs(all_residual) < 50])),
        "note": "Waveforms are trigger-relative; t0 cannot be recovered from release truth without stored trigger time.",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=32)
    args = parser.parse_args()
    paths = build_waveform_figures(args.release, args.output, args.sample_limit)
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
