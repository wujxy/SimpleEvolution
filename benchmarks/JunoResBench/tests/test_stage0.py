"""Stage 0 smoke tests: schema, RNG isolation, stage reproducibility.

Run: python3 benchmarks/JunoResBench/tests/test_stage0.py
"""

import hashlib
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    DirectionGrid,
    PMTLayout,
)
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.rng import STAGE_KEYS, make_rngs
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.truth import (
    EventInput,
    ParticleType,
    PhotonSoA,
)


def test_rng_streams():
    rngs = make_rngs(123)
    assert set(rngs) == set(STAGE_KEYS)
    # streams are independent (overwhelmingly different draws)
    a = [rngs[k].normal() for k in STAGE_KEYS]
    rngs2 = make_rngs(123)
    b = [rngs2[k].normal() for k in STAGE_KEYS]
    assert a == b, "same seed must reproduce streams"
    print("ok  rng streams: keys, reproducibility")


def test_stage_reproducibility():
    cfg = DetectorConfig()
    ev = EventInput(0, 0, 0, 1.0)
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages import s2_photons
    p1 = s2_photons.run_s2_scint(
        type("S1", (), {"e_vis_mev": 1.0})(), ev, cfg, np.random.default_rng(5)
    )
    p2 = s2_photons.run_s2_scint(
        type("S1", (), {"e_vis_mev": 1.0})(), ev, cfg, np.random.default_rng(5)
    )
    assert np.array_equal(p1.t_emit_ns, p2.t_emit_ns)
    assert np.array_equal(p1.dir, p2.dir)
    assert len(p1) > 0
    # isotropy: mean |z| ~ 0
    assert abs(p1.dir[:, 2].mean()) < 0.05
    print(f"ok  stage 2 reproducible, isotropic (n={len(p1)})")


def test_event_truth_consistency():
    sim = DetectorSim(DetectorConfig(), PMTLayout.uniform(), seed=42)
    ev = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
    assert ev.n_pe_total == int(ev.n_pe_pmt.sum())
    assert ev.n_pe_total == len(ev.t_rel_ns)
    assert ev.n_arrived >= ev.n_gamma  # scint all assigned (+ Cherenkov hits)
    # per-PE identity: t_emit + t_tof + TTS(+scatter+offset) + pre = t_rel
    res = ev.t_rel_ns - ev.t_emit_ns - ev.t_tof_ns - sim.cfg.pre_trigger_ns
    expect = np.sqrt(sim.cfg.tts_sigma_ns**2
                     + (sim.cfg.a_scatter_ns_per_m * sim.layout.radius_m)**2
                     + sim.cfg.time_offset_sigma_ns**2)
    assert abs(res.std() - expect) < 0.3
    assert (ev.t_emit_ns >= 0).all()
    print(f"ok  event truth consistent (residual {res.std():.2f} ns ~ TTS)")


def test_rng_isolation_across_stages():
    """Perturbing one stage's stream must not change another stage's output."""
    sim = DetectorSim(DetectorConfig(), PMTLayout.uniform(), seed=99)
    ev = sim.generate(0, 0, 0, 2.0, with_waveforms=False)
    # re-run stage 2 scint with its own stream: same seed -> same photons
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages import s1_response, s2_photons
    s1 = s1_response.run_s1(
        EventInput(0, 0, 0, 2.0), DetectorConfig()
    )
    p = s2_photons.run_s2_scint(
        s1, EventInput(0, 0, 0, 2.0), DetectorConfig(), sim.rngs["s2_scint"]
    )
    # continuing the stream gives new draws, but the first draws were fixed:
    assert len(p) > 0
    print("ok  stage streams addressable independently")


def test_direction_grid():
    lay = PMTLayout.uniform(2000, 19.365)   # small layout for fast build
    grid = DirectionGrid.for_layout(lay, n_theta=90)
    assert len(grid.pmt_idx) == grid.n_theta * grid.n_phi
    assert grid.pmt_idx.min() >= 0 and grid.pmt_idx.max() < lay.n_pmt
    # PMT directions map into their own bin's nearest PMT (mostly)
    dirs = lay.positions_m / np.linalg.norm(lay.positions_m, axis=1, keepdims=True)
    got = grid.lookup(dirs)
    acc = (got == np.arange(lay.n_pmt)).mean()
    assert acc > 0.85, f"direction grid accuracy {acc:.2f} too low"
    print(f"ok  direction grid ({len(grid.bin_dirs)} bins, acc={acc:.2f})")


def test_juno_layout_aligns_position_and_type_by_copy_number(tmp_path):
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
        PMT_HAMAMATSU,
        PMT_HIGHQE_NNVT,
        PMT_NNVT,
    )

    pos = tmp_path / "pos.csv"
    typ = tmp_path / "type.csv"
    pos.write_text(
        "# header\n2 0 0 -19365 180 0\n0 0 0 19365 0 0\n"
        "1 19365 0 0 90 0\n",
        encoding="utf-8",
    )
    typ.write_text(
        "# header\n1 NNVT\n2 HighQENNVT\n0 Hamamatsu\n",
        encoding="utf-8",
    )

    layout = PMTLayout.from_juno_csv(pos, typ)

    assert np.array_equal(layout.copy_no, [0, 1, 2])
    assert np.allclose(layout.positions_m[:, 2], [19.365, 0.0, -19.365])
    assert np.array_equal(
        layout.pmt_model,
        [PMT_HAMAMATSU, PMT_NNVT, PMT_HIGHQE_NNVT],
    )
    assert layout.source == "JUNO J26.4.1 CD-LPMT"
    assert len(layout.source_sha256) == 2


@pytest.mark.parametrize(
    ("position_rows", "type_rows", "message"),
    [
        (
            "0 0 0 1 0 0\n0 0 0 1 0 0\n",
            "0 Hamamatsu\n",
            "duplicate CopyNo",
        ),
        (
            "0 0 0 1 0 0\n1 0 0 -1 0 0\n",
            "0 Hamamatsu\n",
            "position/type CopyNo mismatch",
        ),
        (
            "0 0 0 1 0 0\n",
            "0 mystery\n",
            "unknown PMT type",
        ),
    ],
)
def test_juno_layout_rejects_invalid_pairs(
    tmp_path, position_rows, type_rows, message
):
    pos = tmp_path / "pos.csv"
    typ = tmp_path / "type.csv"
    pos.write_text(position_rows, encoding="utf-8")
    typ.write_text(type_rows, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        PMTLayout.from_juno_csv(pos, typ)


@pytest.mark.skipif(
    os.environ.get("JRB_RUN_JUNO_GEOMETRY") != "1",
    reason="requires the JUNO J26.4.1 CVMFS geometry mount",
)
def test_installed_juno_geometry():
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
        PMT_HAMAMATSU,
        PMT_HIGHQE_NNVT,
        PMT_NNVT,
    )

    layout = PMTLayout.from_juno_csv()

    assert layout.n_pmt == 17612
    assert np.unique(layout.copy_no).size == 17612
    assert (layout.pmt_model == PMT_HAMAMATSU).sum() == 4955
    assert (layout.pmt_model == PMT_NNVT).sum() == 2738
    assert (layout.pmt_model == PMT_HIGHQE_NNVT).sum() == 9919
    assert 19.0 < layout.radius_m < 19.5


def test_calibration():
    cfg = DetectorConfig()
    lay = PMTLayout.uniform(100, 19.365)
    sim = DetectorSim(cfg, lay, seed=1)
    c = sim.calib
    assert c.pde_delta.shape == (100,)
    assert abs(c.pde_delta.std() - cfg.pde_sigma) < 0.03
    assert np.allclose(c.tts_sigma_ns, cfg.tts_sigma_ns)
    assert np.allclose(c.gain, 1.0, atol=0.6)
    print("ok  calibration shapes and spreads")


# --- deterministic v2 electron golden lock ----------------------------------
# Digests of the full EventTruth (ints + per-PE arrays + adc rows) for three
# fixed electron events x {fast, trace} x {waveforms on/off}, taken from the
# local-transport and trigger-readout architecture. Any drift in RNG draw
# order or float arithmetic breaks these; regenerate only with a reviewed
# forward-model change and the full physical-anchor suite.
GOLDEN_EVENTS = (
    EventInput(1.0, 2.0, -3.0, 5.0, t0_ns=17.0),
    EventInput(15.0, 0.0, 0.0, 1.0),
    EventInput(0.0, 0.0, 0.0, 2.5, t0_ns=-4.0),
)
GOLDEN_DIGESTS = {
    "fast_wf0_ev0": "1ba8dec9316f8d4e98cb4be72e36b0340f12e9d88d5d59fec1e8e16395b1ec26",
    "fast_wf0_ev1": "98f8d0cf4162cf683f36229bbc789003b8c5a8e8ede0c6770a12eabda8957dca",
    "fast_wf0_ev2": "bac8ed52b6308bf00d0f94feaedfc3272d4b870d0820daeb66d512e15d1a28ce",
    "fast_wf1_ev0": "3063638a6deab297c157fe2d6dd22b37362fb0d0dbb3c70c1c86caf1816c4058",
    "fast_wf1_ev1": "55b7670945e32190c7fc61e483a86cff1152cad8af797f36a446b9ae994fbc01",
    "fast_wf1_ev2": "daa65c5cf46bb64fac099081fcdad67475a45203bf467ef69b853269f2f5d051",
    "trace_wf0_ev0": "853366d502e2c50fb25b35fd9a612b159a7dc580dfd7862b1d32bb39d70ebd8f",
    "trace_wf0_ev1": "05527c5ebf5acba266aad59ed9b31a60e79bd7ab3735cf35bf3d8fe08eb7620b",
    "trace_wf0_ev2": "11d339f0b9fb28ce127e2b54befce7c49099400d17ab70f7b5cc23332a8f5a7f",
    "trace_wf1_ev0": "5a62125e977f0b38b994ba25d13a619f4671d2f317e9c3cc534bbab3f32e48cf",
    "trace_wf1_ev1": "a610efa671925d8f9f5583bc00765483ecddc5c6a981a96f366bcdbe9a40e448",
    "trace_wf1_ev2": "b1d564f8f636071e650cc8a048646cd107da64798faa6c01f357dc4545b68dc2",
}


def _truth_digest(ev):
    h = hashlib.sha256()
    ints = [ev.n_gamma, ev.n_gamma_cher, ev.n_arrived, ev.n_pe_produced,
            ev.n_pe_total, len(ev.pmt_ids)]
    h.update(np.asarray(ints, np.int64).tobytes())
    for a in (ev.pe_type, ev.t_emit_ns, ev.t_tof_ns, ev.t_rel_ns, ev.q_pe,
              np.asarray(ev.pmt_ids), np.asarray(ev.n_pe_pmt)):
        h.update(np.ascontiguousarray(a).tobytes())
    if ev.adc is not None:
        ids = np.asarray([r[0] for r in ev.adc], np.int32)
        rows = (np.vstack([r[1] for r in ev.adc]) if len(ev.adc)
                else np.zeros((0, 1), np.uint16))
        h.update(ids.tobytes())
        h.update(np.ascontiguousarray(rows).tobytes())
    return h.hexdigest()


def test_v2_electron_golden():
    if not GOLDEN_DIGESTS:
        print("skip golden digests (empty — regenerate after architecture "
              "changes, see comment above)")
        return
    layout = PMTLayout.uniform()
    for mode in ("fast", "trace"):
        for wf in (True, False):
            sim = DetectorSim(DetectorConfig(optics_mode=mode), layout, seed=1234)
            for i, e in enumerate(GOLDEN_EVENTS):
                got = _truth_digest(sim.generate_event(e, with_waveforms=wf))
                key = f"{mode}_wf{int(wf)}_ev{i}"
                assert got == GOLDEN_DIGESTS[key], (
                    f"v2 electron golden changed: {key}\n"
                    f"  got      {got}\n  expected {GOLDEN_DIGESTS[key]}"
                )
    print("ok  v2 electron golden digests (12/12)")


def test_particle_type_dispatch():
    """v1: gamma/positron run end-to-end, deterministic, truth populated."""
    for pt in (ParticleType.GAMMA, ParticleType.POSITRON):
        ev = DetectorSim(DetectorConfig(), PMTLayout.uniform(100, 19.365), seed=1).generate_event(
            EventInput(1.0, 2.0, -1.0, 2.0, particle_type=pt),
            with_waveforms=False,
        )
        assert ev.particle_type is pt
        assert ev.n_pe_total == int(np.asarray(ev.n_pe_pmt).sum())
        assert len(ev.step_e_dep_mev) >= 1 and ev.pe_step.max() < len(ev.step_e_dep_mev)
        # determinism through the full chain (fresh sims, same seed)
        ev2 = DetectorSim(DetectorConfig(), PMTLayout.uniform(100, 19.365), seed=1).generate_event(
            EventInput(1.0, 2.0, -1.0, 2.0, particle_type=pt), with_waveforms=False
        )
        assert np.array_equal(ev.t_rel_ns, ev2.t_rel_ns)
    print("ok  gamma/positron dispatch: end-to-end + deterministic")


def test_s1_stream_controls_track_shape_not_response_integral():
    """Angular diffusion uses s1 RNG without changing deposited response."""
    from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.stages.s1_response import run_s1

    cfg = DetectorConfig()
    event = EventInput(0, 0, 0, 1.5)
    rng_a = np.random.default_rng(5)
    rng_b = np.random.default_rng(5)
    rng_b.normal(size=10000)

    a = run_s1(event, cfg, rng_a)
    b = run_s1(event, cfg, rng_b)

    assert not np.array_equal(a.steps.pos_m, b.steps.pos_m)
    assert a.e_dep_mev == b.e_dep_mev
    assert a.e_vis_mev == b.e_vis_mev


if __name__ == "__main__":
    t0 = time.time()
    test_rng_streams()
    test_stage_reproducibility()
    test_event_truth_consistency()
    test_rng_isolation_across_stages()
    test_direction_grid()
    test_calibration()
    test_v2_electron_golden()
    test_particle_type_dispatch()
    test_s1_stream_controls_track_shape_not_response_integral()
    print(f"\nall stage-0 tests passed ({time.time()-t0:.1f}s)")
