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
    nl_corr,
    run_s1,
)
from benchmarks.JunoResBench.juno_res_bench.truth import (
    EventInput,
    ParticleType,
)


def test_birks_default_on():
    cfg = DetectorConfig()
    s1 = run_s1(EventInput(0, 0, 0, 1.0), cfg)
    expect = 1.0 / 1.0241 * (1 - cfg.nl_amp * np.exp(-1.0))
    assert abs(s1.e_vis_mev / 1.0 - expect) < 1e-9
    assert abs(s1.e_dep_mev - 1.0) < 1e-12
    print(f"ok  birks+nl ON: E_vis/E_true = {s1.e_vis_mev:.4f} (expect {expect:.4f})")


def test_nl_curve():
    cfg = DetectorConfig()
    e = np.geomspace(0.1, 20.0, 200)
    nl = np.array([nl_corr(x, cfg) for x in e])
    # monotonic increase toward 1, continuous
    assert (np.diff(nl) > 0).all()
    assert abs(nl[-1] - 1.0) < 1e-3
    assert nl[0] < 1.0
    # anchor: ~-0.7% at 1 MeV
    assert abs(nl_corr(1.0, cfg) - (1 - cfg.nl_amp * np.exp(-1.0))) < 1e-9
    print(f"ok  nl curve: nl(0.1)={nl[0]:.4f}, nl(1)={nl_corr(1.0, cfg):.4f}, "
          f"nl(10)={nl_corr(10.0, cfg):.4f}")


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
    n = 500
    # energy conservation: sum(step deposits) + escaped == E_true
    for pt, e0 in ((ParticleType.GAMMA, 1.46), (ParticleType.POSITRON, 1.0)):
        for i in range(n):
            s1 = run_s1(EventInput(0, 0, 0, e0, particle_type=pt), cfg,
                        np.random.default_rng(2000 + i))
            expect = e0 + (1.021998 if pt is ParticleType.POSITRON else 0.0)
            assert abs(s1.steps.e_dep_mev.sum() + s1.e_escape_mev - expect) < 1e-6
            # per-step visible energy uses the same quench/nl formula
            nl = np.asarray([cfg.nl_correction(float(x)) for x in s1.steps.e_dep_mev])
            ref = s1.steps.e_dep_mev / (1.0 + cfg.birks_kB_ddx) * nl
            assert np.allclose(s1.steps.e_vis_mev, ref, rtol=0, atol=1e-12)
    print("ok  gamma/positron energy conservation + per-step quench formula")

    # escape rises steeply within ~1 mfp of the LS boundary (17.7 m)
    def esc_at(r, e=2.0, m=300):
        return float(np.mean([
            run_s1(EventInput(r, 0, 0, e, particle_type=ParticleType.GAMMA), cfg,
                   np.random.default_rng(3000 + i)).e_escape_mev
            for i in range(m)]))

    assert esc_at(0.0) == 0.0 and esc_at(14.0) < 1e-9
    assert esc_at(17.5) > 0.02                       # >2% at 20 cm from wall
    # chain length in the expected range at 1 MeV
    steps = [run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.GAMMA), cfg,
                    np.random.default_rng(4000 + i)).steps.n_steps
             for i in range(200)]
    assert 3 < np.mean(steps) < 40
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
    n = 400
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
    assert abs(delayed / n - exp_delayed) < 0.05, (delayed / n, exp_delayed)
    print(f"ok  positron annihilation: delayed fraction {delayed/n:.3f} "
          f"(expect ~{exp_delayed:.3f}), annihilation energy conserved")


def test_end_to_end_linearity():
    """pe/E should now show mild nonlinearity (rising toward high E)."""
    cfg = DetectorConfig()
    sim = DetectorSim(cfg, PMTLayout.uniform(), seed=11)
    rng = np.random.default_rng(3)
    out = {}
    for e_true in (0.5, 1.0, 5.0):
        pe = [
            sim.generate(
                *(np.array([0.0, 0.0, 0.0]) + 2.0 * rng.uniform(-1, 1, 3)),
                e_true, with_waveforms=False,
            ).n_pe_total
            / e_true
            for _ in range(300)
        ]
        out[e_true] = float(np.mean(pe))
    # quench+nl make low-E yield lower pe/MeV than high-E
    assert out[0.5] < out[1.0] < out[5.0], f"nonlinearity direction wrong: {out}"
    print(f"ok  pe/MeV nonlinearity: 0.5MeV={out[0.5]:.1f} < "
          f"1MeV={out[1.0]:.1f} < 5MeV={out[5.0]:.1f}")


if __name__ == "__main__":
    test_birks_default_on()
    test_nl_curve()
    test_dispatch_guard()
    test_gamma_chain()
    test_positron_annihilation()
    test_end_to_end_linearity()
    print("\nall stage-1 tests passed")
