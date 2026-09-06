import json
from pathlib import Path

import numpy as np

from benchmarks.JunoResBench.scripts.plot_electron_single_site_waveforms import (
    FIGURE_NAMES,
    ReleaseWaveforms,
    ShardWaveforms,
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


def _write_dense_split(split: Path, energies, vertices, truth=None, rng_seed=100):
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
    if truth is not None:
        np.savez(split / "truth.npz", **truth)


def _synthetic_release(root: Path):
    """Two in-place shards per population, bound by publish_shards."""
    from types import SimpleNamespace

    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import PMTLayout
    from benchmarks.JunoResBench.world_generator.shard.merge_release import publish_shards

    calib_energies = np.asarray([0.511, 1.022, 2.223, 4.44])
    final_energies = np.asarray([1.0, 2.0, 5.0, 5.0, 7.0, 1.2, 4.4, 9.5])
    final_roles = np.asarray([0, 0, 0, 0, 0, 1, 1, 1], dtype=np.int8)
    final_vertices = np.zeros((len(final_energies), 3))
    final_vertices[1, 0] = 14.0
    final_vertices[3, 1] = 8.0
    final_vertices[6, 0] = 6.0
    dev_energies = np.asarray([1.5, 3.3, 6.0, 8.2])
    dev_vertices = np.zeros((len(dev_energies), 3))
    dev_vertices[:, 0] = np.linspace(2.0, 12.0, len(dev_energies))

    shards_root = root / "shards"
    release = root
    (release / "public").mkdir(parents=True)
    (release / "private").mkdir(parents=True)
    np.savez(
        release / "public/detector_geometry.npz",
        pmt_positions_m=positions_fixture,
    )

    halves = {}
    for shard in (0, 1):
        lo, hi = shard * 2, shard * 2 + 2
        flo, fhi = shard * 4, shard * 4 + 4
        calib_dir = shards_root / f"shard_{shard:03d}" / "calibration"
        dev_dir = shards_root / f"shard_{shard:03d}" / "dev"
        final_dir = shards_root / f"shard_{shard:03d}" / "final"
        _write_dense_split(
            calib_dir, calib_energies[lo:hi], np.zeros((2, 3))
        )
        _write_dense_split(dev_dir, dev_energies[lo:hi], dev_vertices[lo:hi])
        _write_dense_split(
            final_dir, final_energies[flo:fhi], final_vertices[flo:fhi]
        )
        halves[shard] = (calib_energies[lo:hi], dev_energies[lo:hi],
                         final_energies[flo:fhi], final_vertices[flo:fhi],
                         final_roles[flo:fhi])

    populations = {}
    for name, source in (
        ("calibration", calib_energies), ("dev", dev_energies),
        ("final", final_energies),
    ):
        populations[name] = {"evt_e_true": source}

    def _truth(shard):
        calib_e, dev_e, final_e, final_v, final_r = halves[shard]
        n = len(final_e)
        return (
            {"evt_e_true": calib_e, "evt_vertex_m": np.zeros((len(calib_e), 3))},
            {"evt_e_true": dev_e, "evt_vertex_m": dev_vertices[shard * 2:shard * 2 + 2]},
            {
                "evt_e_true": final_e,
                "evt_vertex_m": final_v,
                "evt_sample_role": final_r,
                "evt_t0_ns": np.linspace(-10, 10, n),
                "evt_e_escape_mev": np.zeros(n),
                "evt_total_energy": final_e,
                "step_offsets": np.arange(0, 2 * n + 1, 2, dtype=np.int64),
                "step_e_dep_mev": np.column_stack((
                    np.full(n, 0.05), final_e - 0.05)).ravel(),
                "step_e_vis_mev": np.column_stack((
                    np.full(n, 0.05), final_e - 0.05)).ravel()
                    * np.tile([0.80, 0.98], n),
                "step_kinetic_mev": np.tile([0.02, 1.0], n),
            },
        )

    for shard in (0, 1):
        calib_truth, _dev_truth, final_truth = _truth(shard)
        base = shards_root / f"shard_{shard:03d}"
        np.savez(base / "calibration" / "truth.npz", **calib_truth)
        np.savez(base / "final" / "truth.npz", **final_truth)

    layout = PMTLayout.uniform(N_PMT, DetectorConfig().detector_radius_m)
    publish_shards(
        [shards_root / f"shard_{s:03d}" for s in (0, 1)],
        release, SimpleNamespace(task="electron_single_site"),
        {
            "calibration": {
                "evt_e_true": calib_energies,
                "evt_vertex_m": np.zeros((len(calib_energies), 3)),
            },
            "dev": {"evt_e_true": dev_energies,
                    "evt_vertex_m": dev_vertices},
            "final": {
                "evt_e_true": final_energies,
                "evt_vertex_m": final_vertices,
                "evt_sample_role": final_roles,
            },
        },
        DetectorConfig(optics_mode="trace"),
        layout,
    )


def test_builds_bounded_waveform_audit(tmp_path):
    release = tmp_path / "release"
    _synthetic_release(release)

    reader = ShardWaveforms(release / "private/final_shards.json")
    event = reader.read_event(3)
    assert all(isinstance(r.samples, np.memmap) for r in reader.readers)

    paths = build_waveform_figures(release, tmp_path / "figures", sample_limit=8)
    from benchmarks.JunoResBench.scripts.plot_electron_single_site_waveforms import (
        ShardWaveforms as _SW,
    )

    assert set(FIGURE_NAMES) == EXPECTED
    assert set(paths) == EXPECTED
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths.values())
    summary = json.loads((tmp_path / "figures/summary.json").read_text())
    assert summary["events_scanned"] <= 8
    assert summary["events_total_dense_calibration"] == 4
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
    index_path = release / "shards/shard_001/final/index.npz"
    with np.load(index_path) as index:
        arrays = {name: index[name] for name in index.files}
    arrays["segment_sample_offsets"][-1] += 10_000
    np.savez(index_path, **arrays)

    try:
        ShardWaveforms(release / "private/final_shards.json")
    except ValueError as error:
        assert "sample offsets" in str(error)
    else:
        raise AssertionError("invalid offsets were accepted")
