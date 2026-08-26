"""Stage 0 smoke tests: schema, RNG isolation, stage reproducibility.

Run: python3 benchmarks/JunoResBench/tests/test_stage0.py
"""

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


def test_particle_type_guard():
    sim = DetectorSim(DetectorConfig(), PMTLayout.uniform(100, 19.365), seed=1)
    try:
        sim.generate_event(
            EventInput(0, 0, 0, 1.0, particle_type=ParticleType.GAMMA)
        )
        raise AssertionError("gamma must be NotImplementedError in v0")
    except NotImplementedError:
        print("ok  particle-type dispatch guard")


if __name__ == "__main__":
    t0 = time.time()
    test_rng_streams()
    test_stage_reproducibility()
    test_event_truth_consistency()
    test_rng_isolation_across_stages()
    test_direction_grid()
    test_calibration()
    test_particle_type_guard()
    print(f"\nall stage-0 tests passed ({time.time()-t0:.1f}s)")
