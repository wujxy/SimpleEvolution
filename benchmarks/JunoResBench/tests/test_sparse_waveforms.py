"""Tests for the v2 sparse waveform event container."""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.sparse_waveforms import (
    SparseSplit,
    encode_event,
    write_sparse_split,
)
from benchmarks.JunoResBench.juno_res_bench.split_io import load_split


def _waveforms():
    adc = np.full((8, 1024), 16000, dtype=np.uint16)
    for row, center in enumerate(range(100, 500, 50)):
        adc[row, center:center + 5] = 16000 - np.array([8, 20, 40, 20, 8])
    return adc, np.arange(100, 108, dtype=np.int32)


def test_sparse_roi_preserves_all_threshold_crossings():
    adc, pmt_ids = _waveforms()

    sparse = encode_event(
        adc, pmt_ids, baseline=16000, threshold_adc=6, pre=16, post=48
    )
    dense = sparse.to_dense(fill=16000)
    active = (16000 - adc) >= 6

    assert np.array_equal(dense[active], adc[active])
    assert np.array_equal(sparse.pmt_ids, pmt_ids)


def test_sparse_roi_reduces_quiet_waveforms():
    adc, pmt_ids = _waveforms()

    sparse = encode_event(adc, pmt_ids, 16000, 6, 16, 48)

    assert sparse.samples.nbytes < 0.25 * adc.nbytes


def test_overlapping_expanded_regions_are_merged():
    adc = np.full((1, 128), 16000, dtype=np.uint16)
    adc[0, [40, 60]] = 15980

    sparse = encode_event(adc, np.array([7]), 16000, 6, 16, 16)

    assert len(sparse.segment_pmt_ids) == 1
    assert sparse.segment_start_samples.tolist() == [24]


def test_sparse_split_round_trip_is_streamed_and_truth_is_separate(tmp_path):
    adc, pmt_ids = _waveforms()
    observations = [
        encode_event(adc, pmt_ids, 16000, 6, 16, 48),
        encode_event(np.roll(adc, 9, axis=1), pmt_ids, 16000, 6, 16, 48),
    ]
    path = tmp_path / "split"

    write_sparse_split(
        path,
        {"purpose": "test"},
        observations,
        truth={"evt_e_vis": np.array([1.0, 2.0])},
    )
    split = SparseSplit(path)

    assert isinstance(split.samples, np.memmap)
    assert len(split) == 2
    assert [event.samples.size for event in split.iter_events()] == [
        event.samples.size for event in observations
    ]
    assert np.array_equal(next(split.iter_events()).to_dense(), observations[0].to_dense())
    metadata = json.loads((path / "metadata.json").read_text())
    assert metadata["purpose"] == "test"
    assert metadata["threshold_adc"] == 6
    assert metadata["pre_samples"] == 16
    assert metadata["post_samples"] == 48
    assert "evt_e_vis" not in split.metadata
    assert (path / "truth.npz").exists()
    assert isinstance(load_split(path), SparseSplit)
