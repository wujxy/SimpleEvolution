"""Synthetic, type-aware LPMT response anchors."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import build_calibration
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    PMT_HAMAMATSU,
    PMT_HIGHQE_NNVT,
    PMT_NNVT,
    PMTLayout,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.pmt_response import (
    sample_spe_charge,
    sample_transit_time,
)


def _typed_layout(n_each=4000):
    models = np.repeat(
        [PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT], n_each
    ).astype(np.int8)
    return PMTLayout(
        PMTLayout.uniform(len(models)).positions_m,
        pmt_model=models,
    )


def test_type_aware_population_matches_public_anchors():
    layout = _typed_layout()
    cfg = DetectorConfig()
    a = build_calibration(cfg, layout, 0.15, np.random.default_rng(12))
    b = build_calibration(cfg, layout, 0.15, np.random.default_rng(12))

    for name in vars(a):
        assert np.array_equal(getattr(a, name), getattr(b, name))

    h, n, q = (layout.pmt_model == code for code in (
        PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT
    ))
    pde = 1.0 + a.pde_delta
    ratios = np.array([pde[h].mean(), pde[n].mean(), pde[q].mean()])
    assert np.allclose(ratios / ratios[0], [1.0, 0.273 / 0.285, 0.313 / 0.285], atol=0.01)
    assert abs(a.dark_rate_hz[h].mean() / 1e3 - 15.3) < 0.8
    assert abs(a.dark_rate_hz[n].mean() / 1e3 - 31.0) < 1.5
    assert abs(a.tts_sigma_ns[h].mean() - 1.3) < 0.08
    assert abs(a.tts_sigma_ns[n].mean() - 7.0) < 0.3
    assert a.time_offset_ns.std() > 0.5
    assert np.all((a.gain > 0.8) & (a.gain < 1.2))


def test_nnvt_time_response_is_broader_and_non_gaussian():
    layout = _typed_layout(1)
    calib = build_calibration(
        DetectorConfig(), layout, 0.15, np.random.default_rng(3)
    )
    ids_h = np.zeros(120000, dtype=np.int64)
    ids_n = np.ones(120000, dtype=np.int64)
    h = sample_transit_time(calib, ids_h, np.random.default_rng(4))
    n = sample_transit_time(calib, ids_n, np.random.default_rng(5))

    assert 1.1 < h.std() < 1.6
    assert 6.0 < n.std() < 8.0
    assert np.mean(np.abs(n - np.median(n)) > 9.0) > 0.12


def test_spe_charge_is_type_dependent_and_unit_mean():
    layout = _typed_layout(1)
    calib = build_calibration(
        DetectorConfig(), layout, 0.15, np.random.default_rng(7)
    )
    ids_h = np.zeros(150000, dtype=np.int64)
    ids_n = np.ones(150000, dtype=np.int64)
    h = sample_spe_charge(calib, ids_h, np.random.default_rng(8))
    n = sample_spe_charge(calib, ids_n, np.random.default_rng(9))

    assert abs(h.mean() - 1.0) < 0.01
    assert abs(n.mean() - 1.0) < 0.01
    assert 0.25 < h.std() < 0.31
    assert 0.30 < n.std() < 0.36
    assert np.quantile(n, 0.99) > np.quantile(h, 0.99)


def test_typed_response_reaches_end_to_end_waveforms():
    layout = _typed_layout(100)
    sim = DetectorSim(DetectorConfig(), layout, seed=31)
    event = sim.generate(0.0, 0.0, 0.0, 1.0, with_waveforms=True)

    assert event.n_pe_total > 1000
    assert len(event.q_pe) == event.n_pe_total
    assert len(event.adc) == len(event.adc_ids) > 0
    assert set(np.unique(sim.calib.pmt_model)) == {
        PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT
    }


def test_typed_response_preserves_cherenkov_stream_isolation():
    layout = _typed_layout(100)
    on = DetectorSim(DetectorConfig(), layout, seed=41).generate(
        1.0, 2.0, -1.0, 3.0, with_waveforms=False
    )
    off = DetectorSim(
        DetectorConfig(cherenkov_photons_per_m=None), layout, seed=41
    ).generate(1.0, 2.0, -1.0, 3.0, with_waveforms=False)

    assert np.array_equal(
        np.sort(on.t_rel_ns[on.pe_type == 0]), np.sort(off.t_rel_ns)
    )
    assert np.array_equal(
        np.sort(on.q_pe[on.pe_type == 0]), np.sort(off.q_pe)
    )
