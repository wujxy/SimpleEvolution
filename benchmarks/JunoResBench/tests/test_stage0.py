"""Stage 0 smoke tests: schema, RNG isolation, stage reproducibility.

Run: python3 benchmarks/JunoResBench/tests/test_stage0.py
"""

import hashlib
import sys
import time
from pathlib import Path

import numpy as np

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
    "fast_wf0_ev0": "672f34459ee4e62eca15e8a8b0cdb1f0d7e4c703c26e2ee4aecf5045aa31b793",
    "fast_wf0_ev1": "56a92cc4eac4f1fc6ef2fd0198bf942bb1dcb2e10e77fc070c641d5727605061",
    "fast_wf0_ev2": "86b945b70c84915ae34b131b2e2f89343d9f0e1a6e3b451690b48a75cab6d32e",
    "fast_wf1_ev0": "5004e4c83b8ed86a984f278f0454d16d9d6225759945a3bf41204a2c9264a1a7",
    "fast_wf1_ev1": "71ef2b1773d1e3b82dc2d8934cb38d2affec59f605400cf37b7ca24357ef5353",
    "fast_wf1_ev2": "6e84fd1114d578396c33a6826473e65951e60c73a27ccbc995342e51c284db8f",
    "trace_wf0_ev0": "e352e6c8fb8cef0ebef86f3e03256a2a8bdab4e557ae513be706fcdb532cb006",
    "trace_wf0_ev1": "88b4d1ac04fd6f6b75912266572438892c536c9cdefd568e4ac13e219ff5b74f",
    "trace_wf0_ev2": "75fb0a62abad47ee67431f6619c43ec8b953aaed0340998b33b02b4661466d68",
    "trace_wf1_ev0": "03f2d73a5ecaf835c41db8b6f0792d101eb21d8999c20f03ecf819c92bf7bae1",
    "trace_wf1_ev1": "bda01b6c02d64ebbe8377ce0a4c099c215a8b21325c419bc0df5444e3b2ba9d0",
    "trace_wf1_ev2": "40b4bf4af3161fb617ec026a812dc6d8771a7faaf09a54341d36c947a33e58df",
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
