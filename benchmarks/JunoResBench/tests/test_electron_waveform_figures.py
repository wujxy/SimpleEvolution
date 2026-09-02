import json
from pathlib import Path

import numpy as np

from benchmarks.JunoResBench.scripts.plot_electron_single_site_waveforms import (
    FIGURE_NAMES,
    ReleaseWaveforms,
    build_waveform_figures,
)


EXPECTED = {
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
}


def _synthetic_release(root: Path, bad_roi=False, public_vertices=True):
    split = root / "public/dev"
    split.mkdir(parents=True)
    (root / "private").mkdir()
    n_event, n_pmt, n_sample = 12, 12, 128
    positions = np.column_stack((
        np.cos(np.linspace(0, 2 * np.pi, n_pmt, endpoint=False)),
        np.sin(np.linspace(0, 2 * np.pi, n_pmt, endpoint=False)),
        np.linspace(-0.8, 0.8, n_pmt),
    ))
    positions *= 19.0 / np.linalg.norm(positions, axis=1)[:, None]
    np.savez(root / "public/detector_geometry.npz", pmt_positions_m=positions)
    energies = np.tile(np.arange(1.0, 7.0), 2)
    vertices = np.column_stack((np.linspace(0, 14, n_event), np.zeros((n_event, 2))))
    public_truth = dict(
        evt_e_true=energies,
        evt_e_vis=energies * 0.975,
        evt_sample_role=np.zeros(n_event, dtype=np.int8),
    )
    if public_vertices:
        public_truth["evt_vertex_m"] = vertices
    np.savez(split / "truth.npz", **public_truth)
    step_offsets = np.arange(0, 2 * n_event + 1, 2, dtype=np.int64)
    step_dep = np.column_stack((np.full(n_event, 0.05), energies - 0.05)).ravel()
    np.savez(
        root / "private/truth.npz",
        evt_e_true=energies,
        evt_vertex_m=vertices,
        evt_t0_ns=np.linspace(-10, 10, n_event),
        evt_e_escape_mev=np.zeros(n_event),
        evt_total_energy=energies,
        step_offsets=step_offsets,
        step_e_dep_mev=step_dep,
        step_e_vis_mev=step_dep * np.tile([0.80, 0.98], n_event),
        step_kinetic_mev=np.tile([0.02, 1.0], n_event),
    )

    event_offsets = [0]
    sample_offsets = [0]
    pmt_ids, starts, blocks = [], [], []
    for event in range(n_event):
        for pmt in range(3 + int(energies[event])):
            width = n_sample if bad_roi else 14 + pmt % 3
            pulse = np.r_[np.zeros(3), -10 * np.arange(1, 7), -10 * np.arange(5, 0, -1)]
            if bad_roi:
                block = np.zeros(width, dtype=np.int16)
                block[50:50 + len(pulse)] = pulse
            else:
                block = np.pad(pulse, (0, width - len(pulse))).astype(np.int16)
            pmt_id = (pmt + event) % n_pmt
            pmt_ids.append(pmt_id)
            starts.append(0 if bad_roi else int(
                10 + np.linalg.norm(positions[pmt_id] - vertices[event])
            ))
            blocks.append(block)
            sample_offsets.append(sample_offsets[-1] + width)
        event_offsets.append(len(pmt_ids))
    np.savez(
        split / "index.npz",
        event_segment_offsets=np.asarray(event_offsets, dtype=np.int64),
        segment_sample_offsets=np.asarray(sample_offsets, dtype=np.int64),
        segment_pmt_ids=np.asarray(pmt_ids, dtype=np.int32),
        segment_start_samples=np.asarray(starts, dtype=np.int16),
    )
    np.save(split / "segment_samples.npy", np.concatenate(blocks))
    (split / "metadata.json").write_text(json.dumps({
        "baseline": 4784,
        "n_events": n_event,
        "n_samples": n_sample,
        "threshold_adc": 6,
        "pre_samples": 16,
        "post_samples": 48,
    }))


def test_builds_bounded_waveform_audit(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)

    reader = ReleaseWaveforms(release / "public/dev")
    event = reader.read_event(3)
    assert isinstance(reader.samples, np.memmap)
    assert event.samples.size < reader.samples.size

    paths = build_waveform_figures(release, tmp_path / "figures", sample_limit=8)

    assert set(FIGURE_NAMES) == EXPECTED
    assert set(paths) == EXPECTED
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths.values())
    summary = json.loads((tmp_path / "figures/summary.json").read_text())
    assert summary["events_scanned"] <= 8
    assert summary["waveform_samples_read"] < reader.samples.size
    assert 0 <= summary["raw_roi_start_zero_fraction"] <= 1
    assert 0 <= summary["raw_roi_near_full_window_fraction"] <= 1
    assert np.isfinite(summary["sparse_to_stored_dense_ratio"])
    assert np.isfinite(summary["charge_energy_correlation"])
    assert np.isfinite(summary["time_distance_slope_ns_per_m"])


def test_plotter_is_independent_of_generator_and_copying():
    source = Path(
        "benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py"
    ).read_text(encoding="utf-8")
    assert "world_generator" not in source
    assert "shutil" not in source
    assert "copyfile" not in source


def test_owner_side_plotter_accepts_private_only_vertices(tmp_path):
    release = tmp_path / "release"
    output = tmp_path / "figures"
    _synthetic_release(release, public_vertices=False)

    paths = build_waveform_figures(release, output, sample_limit=8)

    assert set(paths) == EXPECTED


def test_rejects_invalid_sparse_offsets(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)
    index_path = release / "public/dev/index.npz"
    with np.load(index_path) as index:
        arrays = {name: index[name] for name in index.files}
    arrays["segment_sample_offsets"][-1] += 10_000
    np.savez(index_path, **arrays)

    try:
        ReleaseWaveforms(release / "public/dev")
    except ValueError as error:
        assert "sample offsets" in str(error)
    else:
        raise AssertionError("invalid offsets were accepted")
