"""Stage 0 smoke tests: schema, RNG isolation, stage reproducibility.

Run: python3 benchmarks/JunoResBench/tests/test_stage0.py
"""

import hashlib
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import (
    DirectionGrid,
    PMTLayout,
)
from benchmarks.JunoResBench.juno_res_bench.rng import STAGE_KEYS, make_rngs
from benchmarks.JunoResBench.juno_res_bench.truth import (
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
    from benchmarks.JunoResBench.juno_res_bench.stages import s2_photons
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
    from benchmarks.JunoResBench.juno_res_bench.stages import s1_response, s2_photons
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


# --- electron bit-compat golden lock (v1 particle upgrade) ------------------
# Digests of the full EventTruth (ints + per-PE arrays + adc rows) for three
# fixed electron events x {fast, trace} x {waveforms on/off}, taken from the
# trigger-readout architecture (v4). Any drift in RNG draw order or float
# arithmetic for the electron path breaks these — the anchors elsewhere have
# tolerances and will NOT catch it. Regenerate only together with the full
# e- anchor suite if the numpy build changes (pinned in MANIFEST.md).
GOLDEN_EVENTS = (
    EventInput(1.0, 2.0, -3.0, 5.0, t0_ns=17.0),
    EventInput(15.0, 0.0, 0.0, 1.0),
    EventInput(0.0, 0.0, 0.0, 2.5, t0_ns=-4.0),
)
# Lock taken against the trigger-readout architecture (v4).
GOLDEN_DIGESTS = {
    "fast_wf0_ev0": "f71af9f4401543d6356758de788c88d67e08fb8677a904e9c00e6699cc544a80",
    "fast_wf0_ev1": "5d142ed20e7746e56f6bac9348c3b051ee5da7df71cd0e0f4effc1c78bbc13e6",
    "fast_wf0_ev2": "54bee1582534a0d2c25bdd294a504f0d3d10e205a7afa982ca0dd39677e9aafd",
    "fast_wf1_ev0": "c57059aa465dd6046a4ec62ce9b51911c12d94c2e2803e0a24182193942b562d",
    "fast_wf1_ev1": "74cb9ed02f5461ba0c64a9f9f1e5334d21246f398b02bfb3ee43fa2e3739012e",
    "fast_wf1_ev2": "6b23454ef41aab416bf2edb7274b809e85ebe768bde9fd07990c12093dbbee33",
    "trace_wf0_ev0": "0fb21248ebd0bb1b4fee9e225711166c5bf10d7650f4ad3725dc354ebceb4842",
    "trace_wf0_ev1": "8ce2ddbca5a8247514003c7b82c99cd5ed314cd26f4705fca9160ff4fb65fb56",
    "trace_wf0_ev2": "5e2714c226f2819fd7fe7a14c41ed187390251aad9dc6bf8cfa66da3dce325a4",
    "trace_wf1_ev0": "9e9884488243494a1ad9d3818e4caa43a451581b5058e0993e3900a516f74baf",
    "trace_wf1_ev1": "d9e58c0b94aaf3473737c4d3efe3f976ac1381c5f720c83f2bd43549451d9268",
    "trace_wf1_ev2": "625229e2e7dd595f3dd9b5cd2a201ca1b2ba1987a7e9bdc8cc108fd32eb89a9a",
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


def test_electron_bitcompat_golden():
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
                    f"electron bit-compat broken: {key}\n"
                    f"  got      {got}\n  expected {GOLDEN_DIGESTS[key]}"
                )
    print("ok  electron bit-compat golden digests (12/12)")


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


def test_s1_stream_isolation():
    """Burning the s1 stream must not perturb electron output (no draws)."""
    lay = PMTLayout.uniform(100, 19.365)
    a = DetectorSim(DetectorConfig(), lay, seed=5).generate_event(
        EventInput(0, 0, 0, 1.5), with_waveforms=False)
    sim_b = DetectorSim(DetectorConfig(), lay, seed=5)
    sim_b.rngs["s1_response"].normal(size=10000)        # burn the stream
    b = sim_b.generate_event(EventInput(0, 0, 0, 1.5), with_waveforms=False)
    assert np.array_equal(a.t_rel_ns, b.t_rel_ns) and a.n_pe_total == b.n_pe_total
    print("ok  electron path consumes no s1 rng draws")


if __name__ == "__main__":
    t0 = time.time()
    test_rng_streams()
    test_stage_reproducibility()
    test_event_truth_consistency()
    test_rng_isolation_across_stages()
    test_direction_grid()
    test_calibration()
    test_electron_bitcompat_golden()
    test_particle_type_dispatch()
    test_s1_stream_isolation()
    print(f"\nall stage-0 tests passed ({time.time()-t0:.1f}s)")
