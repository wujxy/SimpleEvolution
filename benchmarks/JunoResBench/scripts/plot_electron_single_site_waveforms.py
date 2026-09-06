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


# Core-flow checkpoints: each answers one physics question about the
# generator. Evidence for the owner, not a mechanism showcase.
FIGURE_NAMES = (
    "vertex_distribution",      # 事例是否填满 fiducial 球（部署正确性）
    "energy_radius_coverage",   # 能量×半径覆盖（题库覆盖完整性）
    "radial_light_yield",       # 光收集不均匀性（刻度必须吸收的效应）
    "charge_vs_energy",         # 电荷线性响应（能量信息存在）
    "first_hit_time",           # prompt/晚光结构（时间信息存在）
    "time_vs_distance",         # 首光随距离推迟（光传播正确）
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


def _event_noise_sigma(event: EventWaveforms) -> float:
    """Robust per-event noise sigma from stored baseline residual samples.

    Flat all-zero rows carry no noise information; if they dominate, the
    MAD would collapse to zero, so they are excluded from the estimate.
    """
    residual = np.asarray(event.samples, dtype=float)
    if residual.size == 0:
        return 0.0
    live = residual[np.any(residual != 0.0, axis=1)].ravel() \
        if residual.ndim == 2 else residual[residual != 0.0]
    if live.size < 1_000:
        live = residual.ravel()
    if live.size > 2_000_000:
        live = live[:: live.size // 2_000_000 + 1]
    median = float(np.median(live))
    return float(1.4826 * np.median(np.abs(live - median)))


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
    pulse_threshold = 5.0 * _event_noise_sigma(event)
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
    reader = ReleaseWaveforms(release_root / "private/final")
    truth = _load_npz(release_root / "private/truth.npz")
    geometry = _load_npz(release_root / "public/detector_geometry.npz")
    positions = np.asarray(geometry["pmt_positions_m"], float)
    energy = np.asarray(truth["evt_e_true"], float)
    vertices = np.asarray(truth["evt_vertex_m"], float)
    radius = np.linalg.norm(vertices, axis=1)
    if len(reader) != len(energy):
        raise ValueError("waveform and truth event counts differ")
    role = np.asarray(truth["evt_sample_role"])
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
    sampled_probe = role[selected] == 0
    paths = {}
    _ = median_time  # diagnostic only

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
    scatter = ax.scatter(radius, energy, c=role, s=5, alpha=0.4)
    ax.set(xlabel="vertex radius [m]", ylabel="true energy [MeV]",
           title="Energy-radius coverage: probe and control samples span the fiducial volume")
    fig.colorbar(scatter, ax=ax, label="sample role")
    paths["energy_radius_coverage"] = _save(fig, output, "energy_radius_coverage")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(
        sample_radius[sampled_probe],
        total_charge[sampled_probe] / sample_energy[sampled_probe],
        c=sample_energy[sampled_probe],
        s=28,
    )
    ax.set(xlabel="vertex radius [m]", ylabel="stored pulse integral / MeV [ADC count]",
           title="Radial light yield: geometry-driven nonuniformity must be calibratable")
    paths["radial_light_yield"] = _save(fig, output, "radial_light_yield")

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
    centered_distance = np.concatenate([
        values - values.mean() for values in distance_blocks if len(values)
    ])
    centered_time = np.concatenate([
        values - values.mean() for values in time_blocks if len(values)
    ])
    time_distance_slope = float(
        np.dot(centered_distance, centered_time)
        / np.dot(centered_distance, centered_distance)
    )
    thin = np.linspace(0, len(all_time) - 1, min(50000, len(all_time)), dtype=int)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(all_distance[thin], all_time[thin], s=2, alpha=0.15)
    ax.set(xlabel="vertex-PMT distance [m]", ylabel="first stored pulse sample [ns]",
           title="Time-distance relation: longer optical paths should arrive later")
    paths["time_vs_distance"] = _save(fig, output, "time_vs_distance")

    dense_complete = [
        len(item.event.segment_pmt_ids) == int(reader.metadata["n_pmt"])
        and bool(np.all(np.diff(item.event.segment_sample_offsets) == item.event.n_samples))
        for item in metrics
    ]
    summary = {
        "events_total": len(reader),
        "events_total_dense_final": len(reader),
        "events_total_dense_calibration": len(
            ReleaseWaveforms(release_root / "public/calibration")
        ),
        "events_scanned": len(metrics),
        "dense_channel_completeness": float(np.mean(dense_complete)),
        "charge_energy_correlation": float(np.corrcoef(
            sample_energy[sampled_probe], total_charge[sampled_probe]
        )[0, 1]),
        "time_distance_slope_ns_per_m": time_distance_slope,
        "mean_hit_pmts": float(hit_count.mean()),
        "mean_integral_per_mev": float(np.mean(
            total_charge[sampled_probe] / sample_energy[sampled_probe]
        )),
        "pulse_selection_threshold_source": "per-event 5-sigma MAD of stored residuals",
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
