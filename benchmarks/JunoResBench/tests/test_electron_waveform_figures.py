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
    "charge_vs_energy",
    "first_hit_time",
    "time_vs_distance",
}

N_PMT = 12
N_SAMPLE = 128
PULSE = np.r_[np.zeros(3), -10 * np.arange(1, 7), -10 * np.arange(5, 0, -1)]

positions_fixture = np.column_stack((
    np.cos(np.linspace(0, 2 * np.pi, N_PMT, endpoint=False)),
    np.sin(np.linspace(0, 2 * np.pi, N_PMT, endpoint=False)),
    np.linspace(-0.8, 0.8, N_PMT),
))
positions_fixture *= 19.0 / np.linalg.norm(positions_fixture, axis=1)[:, None]


def _write_dense_split(split: Path, energies, vertices, rng_seed=100):
    split.mkdir(parents=True)
    n_event = len(energies)
    event_offsets = [0]
    pmt_ids, starts, sizes = [], [], []
    blocks = []
    for event in range(n_event):
        pulse = (PULSE * (1.0 + float(energies[event]) / 4.0)).astype(np.int16)
        rng = np.random.default_rng(rng_seed + event)
        for pmt in range(N_PMT):
            block = rng.normal(0.0, 3.0, N_SAMPLE).astype(np.int16)
            start = int(10 + np.linalg.norm(positions_fixture[pmt] - vertices[event]))
            block[start : start + len(pulse)] += pulse
            pmt_ids.append(pmt)
            starts.append(0)
            sizes.append(N_SAMPLE)
            blocks.append(block)
        event_offsets.append(len(pmt_ids))
    np.savez(
        split / "index.npz",
        event_segment_offsets=np.asarray(event_offsets, dtype=np.int64),
        segment_sample_offsets=np.concatenate(
            (np.zeros(1, dtype=np.int64), np.cumsum(sizes, dtype=np.int64))
        ),
        segment_pmt_ids=np.asarray(pmt_ids, dtype=np.int32),
        segment_start_samples=np.asarray(starts, dtype=np.int16),
    )
    np.save(split / "segment_samples.npy", np.concatenate(blocks))
    split.joinpath("metadata.json").write_text(json.dumps({
        "storage_format": "jrb_sparse_waveforms_v2",
        "encoding": "dense",
        "baseline": 4784,
        "n_events": n_event,
        "n_pmt": N_PMT,
        "n_samples": N_SAMPLE,
        "threshold_adc": 0,
        "pre_samples": 0,
        "post_samples": 0,
    }))


def _synthetic_release(root: Path):
    final_energies = np.asarray([1.0, 2.0, 5.0, 5.0, 7.0, 1.2, 4.4, 9.5])
    final_roles = np.asarray([0, 0, 0, 0, 0, 1, 1, 1], dtype=np.int8)
    final_vertices = np.zeros((len(final_energies), 3))
    final_vertices[1, 0] = 14.0
    final_vertices[3, 1] = 8.0
    final_vertices[6, 0] = 6.0
    dev_energies = np.asarray([1.5, 3.3, 6.0, 8.2])
    dev_vertices = np.zeros((len(dev_energies), 3))
    dev_vertices[:, 0] = np.linspace(2.0, 12.0, len(dev_energies))
    n_event = len(final_energies)

    (root / "public").mkdir(parents=True)
    (root / "private").mkdir()
    np.savez(
        root / "public/detector_geometry.npz", pmt_positions_m=positions_fixture
    )

    _write_dense_split(
        root / "public/calibration", np.asarray([0.511, 1.022]), np.zeros((2, 3))
    )
    _write_dense_split(root / "public/dev", dev_energies, dev_vertices)
    _write_dense_split(root / "private/final", final_energies, final_vertices)
    np.savez(
        root / "private/truth.npz",
        evt_e_true=final_energies,
        evt_vertex_m=final_vertices,
        evt_sample_role=final_roles,
        evt_t0_ns=np.linspace(-10, 10, n_event),
        evt_e_escape_mev=np.zeros(n_event),
        evt_total_energy=final_energies,
        step_offsets=np.arange(0, 2 * n_event + 1, 2, dtype=np.int64),
        step_e_dep_mev=np.column_stack((
            np.full(n_event, 0.05), final_energies - 0.05
        )).ravel(),
        step_e_vis_mev=np.column_stack((
            np.full(n_event, 0.05), final_energies - 0.05
        )).ravel() * np.tile([0.80, 0.98], n_event),
        step_kinetic_mev=np.tile([0.02, 1.0], n_event),
    )


def test_builds_bounded_waveform_audit(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)

    reader = ReleaseWaveforms(release / "private/final")
    event = reader.read_event(3)
    assert isinstance(reader.samples, np.memmap)

    paths = build_waveform_figures(release, tmp_path / "figures", sample_limit=8)

    assert set(FIGURE_NAMES) == EXPECTED
    assert set(paths) == EXPECTED
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths.values())
    summary = json.loads((tmp_path / "figures/summary.json").read_text())
    assert summary["events_scanned"] <= 8
    assert summary["events_total_dense_calibration"] == 2
    assert summary["dense_channel_completeness"] == 1.0
    assert np.isfinite(summary["charge_energy_correlation"])
    assert np.isfinite(summary["time_distance_slope_ns_per_m"])


def test_dev_split_carries_no_truth(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)

    assert not (release / "public/dev/truth.npz").exists()


def test_plotter_is_independent_of_generator_and_copying():
    source = Path(
        "benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py"
    ).read_text(encoding="utf-8")
    assert "world_generator" not in source
    assert "shutil" not in source
    assert "copyfile" not in source


def test_dense_split_rejects_duplicate_channels(tmp_path):
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.sparse_waveforms import (
        encode_dense_event,
    )

    wave = np.full((4, 16), 4784, dtype=np.uint16)
    with np.testing.assert_raises(ValueError):
        encode_dense_event(wave, np.array([0, 1, 2, 2]), 4784)


def test_rejects_invalid_sparse_offsets(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)
    index_path = release / "private/final/index.npz"
    with np.load(index_path) as index:
        arrays = {name: index[name] for name in index.files}
    arrays["segment_sample_offsets"][-1] += 10_000
    np.savez(index_path, **arrays)

    try:
        ReleaseWaveforms(release / "private/final")
    except ValueError as error:
        assert "sample offsets" in str(error)
    else:
        raise AssertionError("invalid offsets were accepted")
