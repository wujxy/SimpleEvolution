"""Stage 1 unit tests: Birks quenching, nonlinearity curve, dispatch guard.

Run: python3 benchmarks/JunoResBench/tests/test_stage1.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.stages.s1_response import (
    run_s1,
)
from benchmarks.JunoResBench.juno_res_bench.truth import (
    DEPOSITION_KINDS,
    EventInput,
    ParticleType,
)
from benchmarks.JunoResBench.juno_res_bench.stopping_power import birks_visible_mev


def test_positron_primary_is_a_local_track():
    cfg = DetectorConfig()
    event = EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON)

    s1 = run_s1(event, cfg, np.random.default_rng(7))
    primary = s1.steps.kind == DEPOSITION_KINDS["primary"]

    assert primary.sum() > 10
    assert np.isclose(s1.steps.e_dep_mev[primary].sum(), 1.0)
    assert (s1.steps.dedx_mev_cm[primary] > 0).all()
    assert (s1.steps.step_length_m[primary] > 0).all()
    assert np.ptp(
        s1.steps.e_vis_mev[primary] / s1.steps.e_dep_mev[primary]
    ) > 0


def test_local_birks_response_is_stored_per_step():
    cfg = DetectorConfig()
    event = EventInput(0, 0, 0, 0.8, particle_type=ParticleType.POSITRON)

    s1 = run_s1(event, cfg, np.random.default_rng(11))
    expected = birks_visible_mev(
        s1.steps.e_dep_mev,
        s1.steps.dedx_mev_cm,
        cfg.birks_kb_cm_per_mev,
    )

    assert np.allclose(s1.steps.e_vis_mev, expected, rtol=0, atol=1e-12)


def test_detector_truth_preserves_local_track_fields():
    sim = DetectorSim(DetectorConfig(), PMTLayout.uniform(100), seed=19)

    event = sim.generate(
        0, 0, 0, 0.5,
        particle_type=ParticleType.POSITRON,
        with_waveforms=False,
    )

    assert len(event.step_kinetic_mev) == len(event.step_e_dep_mev)
    assert len(event.step_dedx_mev_cm) == len(event.step_e_dep_mev)
    assert len(event.step_length_m) == len(event.step_e_dep_mev)


def test_electron_uses_local_birks_response():
    cfg = DetectorConfig()
    s1 = run_s1(EventInput(0, 0, 0, 1.0), cfg)

    expect = birks_visible_mev(
        s1.steps.e_dep_mev,
        s1.steps.dedx_mev_cm,
        cfg.birks_kb_cm_per_mev,
    ).sum()

    assert abs(s1.e_vis_mev - expect) < 1e-12
    assert abs(s1.e_dep_mev - 1.0) < 1e-12
    assert s1.steps.n_steps > 10


def test_dispatch_guard():
    cfg = DetectorConfig()
    for pt in (ParticleType.GAMMA, ParticleType.POSITRON):
        try:
            run_s1(EventInput(0, 0, 0, 1.0, particle_type=pt), cfg)
            raise AssertionError(f"{pt} needs an rng")
        except ValueError:
            pass
    print("ok  gamma/positron require the s1 rng stream")


def test_gamma_chain():
    cfg = DetectorConfig()
    n = 60
    # energy conservation: sum(step deposits) + escaped == E_true
    for pt, e0 in ((ParticleType.GAMMA, 1.46), (ParticleType.POSITRON, 1.0)):
        for i in range(n):
            s1 = run_s1(EventInput(0, 0, 0, e0, particle_type=pt), cfg,
                        np.random.default_rng(2000 + i))
            expect = e0 + (1.021998 if pt is ParticleType.POSITRON else 0.0)
            assert abs(s1.steps.e_dep_mev.sum() + s1.e_escape_mev - expect) < 1e-6
            # per-step visible energy uses local stopping power
            ref = birks_visible_mev(
                s1.steps.e_dep_mev,
                s1.steps.dedx_mev_cm,
                cfg.birks_kb_cm_per_mev,
            )
            assert np.allclose(s1.steps.e_vis_mev, ref, rtol=0, atol=1e-12)
    print("ok  gamma/positron energy conservation + per-step quench formula")

    # escape rises steeply within ~1 mfp of the LS boundary (17.7 m)
    def esc_at(r, e=2.0, m=40):
        return float(np.mean([
            run_s1(EventInput(r, 0, 0, e, particle_type=ParticleType.GAMMA), cfg,
                   np.random.default_rng(3000 + i)).e_escape_mev
            for i in range(m)]))

    assert esc_at(0.0) == 0.0 and esc_at(14.0) < 1e-9
    assert esc_at(17.5) > 0.02                       # >2% at 20 cm from wall
    # Local charged steps expand each gamma interaction into its secondary
    # electron track; the chain is intentionally much denser than v1.
    steps = [run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.GAMMA), cfg,
                    np.random.default_rng(4000 + i)).steps.n_steps
             for i in range(40)]
    assert 100 < np.mean(steps) < 500
    print(f"ok  gamma escape vs radius; mean steps @1 MeV = {np.mean(steps):.1f}")

    # mean free path: with the PE branch off, lambda = 1/(n_e * sigma_KN)
    from benchmarks.JunoResBench.juno_res_bench.stages.s1_particles import (
        gamma_mfp_m,
        sigma_kn_total,
    )
    cfg_kn = DetectorConfig(gamma_pe_crossover_kev=1e-9)
    for e in (0.02, 0.1, 0.511, 1.0, 5.0):
        lam = gamma_mfp_m(e, cfg_kn)
        assert abs(1.0 / (lam * cfg.ls_electron_density_per_m3)
                   - sigma_kn_total(e)) / sigma_kn_total(e) < 1e-12
    # physical anchor: lambda(1 MeV) ~ 17 cm for LAB-based LS
    assert 0.15 < gamma_mfp_m(1.0, cfg) < 0.20
    print(f"ok  mfp: KN-consistent; lambda(1 MeV) = {gamma_mfp_m(1.0, cfg)*100:.1f} cm")


def test_positron_annihilation():
    cfg = DetectorConfig()
    n = 80
    for i in range(n):
        s1 = run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON),
                    cfg, np.random.default_rng(5000 + i))
        st = s1.steps
        assert st.kind[0] == 0                          # primary deposit first
        m = st.kind >= 3
        assert m.any()                                  # annihilation gammas
        # energy in annihilation-origin steps + escape == 2 x 511 keV
        assert abs(st.e_dep_mev[m].sum() + s1.e_escape_mev - 1.021998) < 1e-6
    # o-Ps delayed fraction at a 2 ns tag threshold (flight-time ~0.4 ns
    # contamination negligible). The 2.2% 3-gamma branch shares the o-Ps
    # delay and the conservation assert above already exercises it.
    thr = 2.0
    delayed = 0
    for i in range(n):
        s1 = run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON),
                    cfg, np.random.default_rng(6500 + i))
        m = s1.steps.kind >= 3
        if s1.steps.t_ns[m].min() > thr:
            delayed += 1
    exp_delayed = cfg.ops_fraction * np.exp(-thr / cfg.ops_tau_ns)
    assert abs(delayed / n - exp_delayed) < 0.10, (delayed / n, exp_delayed)
    print(f"ok  positron annihilation: delayed fraction {delayed/n:.3f} "
          f"(expect ~{exp_delayed:.3f}), annihilation energy conserved")


def test_end_to_end_linearity():
    """Integrated local Birks response suppresses low-energy electrons."""
    cfg = DetectorConfig()
    out = {}
    for e_true in (0.05, 0.5, 5.0):
        s1 = run_s1(EventInput(0, 0, 0, e_true), cfg)
        out[e_true] = s1.e_vis_mev / e_true
    assert out[0.05] < out[0.5] < out[5.0], out


if __name__ == "__main__":
    test_electron_uses_local_birks_response()
    test_dispatch_guard()
    test_gamma_chain()
    test_positron_annihilation()
    test_end_to_end_linearity()
    print("\nall stage-1 tests passed")
