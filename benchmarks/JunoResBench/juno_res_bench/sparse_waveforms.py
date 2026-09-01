"""Sparse, event-streamable waveform storage for JunoResBench v2."""

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SparseEvent:
    """Merged waveform regions for one event."""

    pmt_ids: np.ndarray
    segment_pmt_ids: np.ndarray
    segment_start_samples: np.ndarray
    segment_sample_offsets: np.ndarray
    samples: np.ndarray
    baseline: int
    n_samples: int
    threshold_adc: int
    pre_samples: int
    post_samples: int

    def to_dense(self, fill=None):
        """Materialize stored rows, filling samples outside ROIs."""
        fill_value = self.baseline if fill is None else int(fill)
        dense = np.full(
            (len(self.pmt_ids), self.n_samples), fill_value, dtype=np.uint16
        )
        rows = {int(pmt_id): row for row, pmt_id in enumerate(self.pmt_ids)}
        for segment, pmt_id in enumerate(self.segment_pmt_ids):
            start = int(self.segment_start_samples[segment])
            sample_lo = int(self.segment_sample_offsets[segment])
            sample_hi = int(self.segment_sample_offsets[segment + 1])
            stop = start + sample_hi - sample_lo
            values = fill_value + self.samples[sample_lo:sample_hi].astype(np.int32)
            dense[rows[int(pmt_id)], start:stop] = values.astype(np.uint16)
        return dense


def _expanded_regions(active, n_samples, pre, post):
    indices = np.flatnonzero(active)
    if indices.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(indices) > 1) + 1
    groups = np.split(indices, breaks)
    expanded = [
        [max(0, int(group[0]) - pre), min(n_samples, int(group[-1]) + post + 1)]
        for group in groups
    ]
    merged = [expanded[0]]
    for start, stop in expanded[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    return merged


def encode_event(adc, pmt_ids, baseline, threshold_adc, pre, post):
    """Encode all downward threshold crossings and their merged ROIs."""
    waveforms = np.asarray(adc)
    channel_ids = np.asarray(pmt_ids, dtype=np.int32)
    if waveforms.ndim != 2 or channel_ids.shape != (len(waveforms),):
        raise ValueError("adc must be [channel, sample] and align with pmt_ids")
    if threshold_adc <= 0 or pre < 0 or post < 0:
        raise ValueError("threshold must be positive and ROI padding non-negative")
    if waveforms.shape[1] > np.iinfo(np.int16).max:
        raise ValueError("waveform is too long for int16 segment starts")

    segment_ids = []
    starts = []
    sample_blocks = []
    offsets = [0]
    for row, pmt_id in zip(waveforms, channel_ids):
        for start, stop in _expanded_regions(
            int(baseline) - row.astype(np.int32) >= threshold_adc,
            waveforms.shape[1],
            int(pre),
            int(post),
        ):
            residual = row[start:stop].astype(np.int32) - int(baseline)
            if residual.size and (
                residual.min() < np.iinfo(np.int16).min
                or residual.max() > np.iinfo(np.int16).max
            ):
                raise ValueError("waveform residual does not fit int16")
            block = residual.astype(np.int16)
            segment_ids.append(pmt_id)
            starts.append(start)
            sample_blocks.append(block)
            offsets.append(offsets[-1] + len(block))

    samples = (
        np.concatenate(sample_blocks)
        if sample_blocks
        else np.empty(0, dtype=np.int16)
    )
    return SparseEvent(
        pmt_ids=channel_ids,
        segment_pmt_ids=np.asarray(segment_ids, dtype=np.int32),
        segment_start_samples=np.asarray(starts, dtype=np.int16),
        segment_sample_offsets=np.asarray(offsets, dtype=np.int64),
        samples=samples,
        baseline=int(baseline),
        n_samples=int(waveforms.shape[1]),
        threshold_adc=int(threshold_adc),
        pre_samples=int(pre),
        post_samples=int(post),
    )


def write_sparse_split(path, meta, observations, truth=None):
    """Write sparse events with one mmap-able sample array."""
    destination = Path(path)
    destination.mkdir(parents=True, exist_ok=True)
    events = list(observations)
    if not events:
        raise ValueError("a sparse split requires at least one event")
    baseline = events[0].baseline
    n_samples = events[0].n_samples
    roi_config = (
        events[0].threshold_adc,
        events[0].pre_samples,
        events[0].post_samples,
    )
    if any(
        event.baseline != baseline
        or event.n_samples != n_samples
        or (
            event.threshold_adc,
            event.pre_samples,
            event.post_samples,
        ) != roi_config
        for event in events
    ):
        raise ValueError("all sparse events must share waveform and ROI configuration")

    event_offsets = [0]
    sample_offsets = [0]
    segment_ids = []
    segment_starts = []
    for event in events:
        event_offsets.append(event_offsets[-1] + len(event.segment_pmt_ids))
        segment_ids.append(event.segment_pmt_ids)
        segment_starts.append(event.segment_start_samples)
        for size in np.diff(event.segment_sample_offsets):
            sample_offsets.append(sample_offsets[-1] + int(size))

    total_samples = sample_offsets[-1]
    sample_path = destination / "segment_samples.npy"
    if total_samples:
        packed = np.lib.format.open_memmap(
            sample_path, mode="w+", dtype=np.int16, shape=(total_samples,)
        )
        cursor = 0
        for event in events:
            stop = cursor + event.samples.size
            packed[cursor:stop] = event.samples
            cursor = stop
        packed.flush()
        del packed
    else:
        np.save(sample_path, np.empty(0, dtype=np.int16))

    np.savez_compressed(
        destination / "index.npz",
        event_segment_offsets=np.asarray(event_offsets, dtype=np.int64),
        segment_sample_offsets=np.asarray(sample_offsets, dtype=np.int64),
        segment_pmt_ids=(
            np.concatenate(segment_ids)
            if segment_ids
            else np.empty(0, dtype=np.int32)
        ),
        segment_start_samples=(
            np.concatenate(segment_starts)
            if segment_starts
            else np.empty(0, dtype=np.int16)
        ),
    )
    metadata = dict(meta)
    metadata.update({
        "storage_format": "jrb_sparse_waveforms_v2",
        "n_events": len(events),
        "baseline": baseline,
        "n_samples": n_samples,
        "threshold_adc": roi_config[0],
        "pre_samples": roi_config[1],
        "post_samples": roi_config[2],
    })
    (destination / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if truth is not None:
        np.savez_compressed(destination / "truth.npz", **truth)


class SparseSplit:
    """Read a sparse split while memory-mapping its waveform samples."""

    def __init__(self, path):
        self.path = Path(path)
        self.metadata = json.loads(
            (self.path / "metadata.json").read_text(encoding="utf-8")
        )
        with np.load(self.path / "index.npz", allow_pickle=False) as index:
            self.event_segment_offsets = index["event_segment_offsets"]
            self.segment_sample_offsets = index["segment_sample_offsets"]
            self.segment_pmt_ids = index["segment_pmt_ids"]
            self.segment_start_samples = index["segment_start_samples"]
        sample_path = self.path / "segment_samples.npy"
        mmap_mode = "r" if sample_path.stat().st_size > 128 else None
        self.samples = np.load(sample_path, mmap_mode=mmap_mode, allow_pickle=False)

    def __len__(self):
        return len(self.event_segment_offsets) - 1

    def iter_events(self):
        for event_index in range(len(self)):
            segment_lo = int(self.event_segment_offsets[event_index])
            segment_hi = int(self.event_segment_offsets[event_index + 1])
            ids = self.segment_pmt_ids[segment_lo:segment_hi]
            if ids.size:
                first = np.unique(ids, return_index=True)[1]
                pmt_ids = ids[np.sort(first)]
            else:
                pmt_ids = np.empty(0, dtype=np.int32)
            global_offsets = self.segment_sample_offsets[segment_lo:segment_hi + 1]
            sample_lo = int(global_offsets[0])
            sample_hi = int(global_offsets[-1])
            yield SparseEvent(
                pmt_ids=pmt_ids,
                segment_pmt_ids=ids,
                segment_start_samples=self.segment_start_samples[
                    segment_lo:segment_hi
                ],
                segment_sample_offsets=global_offsets - sample_lo,
                samples=self.samples[sample_lo:sample_hi],
                baseline=int(self.metadata["baseline"]),
                n_samples=int(self.metadata["n_samples"]),
                threshold_adc=int(self.metadata["threshold_adc"]),
                pre_samples=int(self.metadata["pre_samples"]),
                post_samples=int(self.metadata["post_samples"]),
            )


__all__ = ["SparseEvent", "SparseSplit", "encode_event", "write_sparse_split"]
