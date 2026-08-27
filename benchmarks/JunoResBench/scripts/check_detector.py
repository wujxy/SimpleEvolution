"""Self-checks and anchor validation for the toy detector.

Run before trusting any dataset:
    python3 benchmarks/JunoResBench/scripts/check_detector.py

Checks (targets from docs/effects.md #5):
  1. mu_pe at center ~1500 pe/MeV
  2. energy resolution (Poisson floor) vs E, linearity < 1%
  3. radial nonuniformity matches the analytic model
  4. hit-time RMS ~6-8 ns at the center
  5. bit-exact reproducibility for a fixed seed
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
from benchmarks.JunoResBench.juno_res_bench.stages.s5_electronics import (
    _find_trigger,
)


def energy_scan(sim, energies, r_max=16.0, n_per=2000):
    print(f"\n== energy scan (uniform vertex r<{r_max} m, {n_per}/point) ==")
    rng = np.random.default_rng(7)
    print(f"{'E (MeV)':>8} {'pe/MeV':>8} {'rel.std':>8} {'1/sqrt(N)':>10}")
    for e_true in energies:
        pe = np.empty(n_per)
        for i in range(n_per):
            u = r_max * rng.uniform() ** (1 / 3)   # uniform in volume
            ct = rng.uniform(-1, 1)
            st = np.sqrt(1 - ct * ct)
            phi = rng.uniform(0, 2 * np.pi)
            vtx = np.array([u * st * np.cos(phi), u * st * np.sin(phi), u * ct])
            ev = sim.generate(*vtx, e_true, with_waveforms=False)
            pe[i] = ev.n_pe_total
        rel = pe.std() / pe.mean()
        print(
            f"{e_true:>8.1f} {pe.mean() / e_true:>8.1f} "
            f"{rel:>8.4f} {1 / np.sqrt(pe.mean()):>10.4f}"
        )


def radial_scan(sim, e_true=1.0, radii=(0, 4, 8, 12, 16), n_per=500):
    print(f"\n== radial nonuniformity (E={e_true} MeV, model excludes CE) ==")
    rng = np.random.default_rng(11)
    cfg = sim.cfg
    print(f"{'r (m)':>6} {'sim pe':>8} {'model':>7} {'ratio':>6}")
    for r in radii:
        vals = []
        for _ in range(n_per):
            ct = rng.uniform(-1, 1)
            st = np.sqrt(1 - ct * ct)
            phi = rng.uniform(0, 2 * np.pi)
            vtx = r * np.array([st * np.cos(phi), st * np.sin(phi), ct])
            vals.append(sim.generate(*vtx, e_true, with_waveforms=False).n_pe_total)
        m = np.mean(vals) / e_true
        # model includes stage-1 factors (Birks + low-E nonlinearity)
        model = (
            cfg.mu_pe_per_mev_center * cfg.mu_pe_ratio(r)
            * cfg.quench(e_true) * cfg.nl_correction(e_true)
        )
        print(f"{r:>6.1f} {m:>8.1f} {model:>7.1f} {m / model:>6.3f}")


def timing_check(sim, n=200):
    print("\n== timing at center ==")
    res, raw = [], []
    for _ in range(n):
        ev = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
        raw.append(ev.t_rel_ns.std())
        # residual after removing known emission time + TOF: pure TTS
        res.append(
            (ev.t_rel_ns + sim.cfg.pre_trigger_ns
             - ev.t_emit_ns - ev.t_tof_ns).std()
        )
    print(f"raw cross-PMT hit-time RMS: {np.mean(raw):.1f} ns (TOF-dominated)")
    print(f"post-TOF/emission residual: {np.mean(res):.2f} ns "
          f"(expect TTS={sim.cfg.tts_sigma_ns} ns)")


def determinism_check(layout_cls, layout_kwargs):
    kw = dict(config=DetectorConfig(), seed=123, **layout_kwargs)
    a = DetectorSim(**kw).generate(1.0, 2.0, -3.0, 5.0)
    b = DetectorSim(**kw).generate(1.0, 2.0, -3.0, 5.0)
    ok = (
        a.n_pe_total == b.n_pe_total
        and np.array_equal(a.t_rel_ns, b.t_rel_ns)
        and np.array_equal(a.q_pe, b.q_pe)
        and all(np.array_equal(x, y) for x, y in zip(a.adc, b.adc))
    )
    print(f"\n== determinism ==\nsame seed -> identical output: {ok}")


def trigger_check(sim, n=200):
    """Trigger architecture anchors: latency, no dark-only firing, t0 task."""
    cfg = sim.cfg
    rng = np.random.default_rng(7)
    lat, t0_ref = [], []
    for i in range(n):
        r = rng.uniform(0, 16 ** 3) ** (1.0 / 3.0)
        ct = rng.uniform(-1, 1)
        phi = rng.uniform(0, 2 * np.pi)
        st = np.sqrt(1 - ct * ct)
        ev = sim.generate(r * st * np.cos(phi), r * st * np.sin(phi), r * ct,
                          float(rng.uniform(1, 8)), t0_ns=float(rng.uniform(0, 1000)),
                          with_waveforms=False)
        lat.append(ev.t_trigger_ns - ev.t0_ns)
        t0_ref.append(ev.t0_ns - (ev.t_trigger_ns - cfg.pre_trigger_ns))
    lat = np.asarray(lat)
    t0_ref = np.asarray(t0_ref)
    fired = (lat > 0).mean()
    # pure-dark streams at the real total rate never cross the threshold
    drng = np.random.default_rng(8)
    dark_fired = sum(
        _find_trigger(drng.uniform(-800, 1500, 970), 0.0, cfg) != 0.0
        for _ in range(50)
    )
    spread = np.quantile(t0_ref, 0.84) - np.quantile(t0_ref, 0.16)
    print(f"\n== trigger readout ==")
    print(f"trigger latency (t_trig - t0): {lat.mean():.0f} +- {lat.std():.0f} ns "
          f"(causal, > 0 for {fired*100:.0f}% of events)")
    print(f"pure-dark spurious triggers: {dark_fired}/50 (expect 0)")
    print(f"window-referenced t0 spread (q84-q16): {spread:.0f} ns "
          f"(dynamic range of the t0 task)")


def particle_check(sim, n=300):
    """v1 anchors: gamma chain, e+/e- scale difference, o-Ps delay."""
    from benchmarks.JunoResBench.juno_res_bench.stages.s1_response import run_s1
    from benchmarks.JunoResBench.juno_res_bench.truth import EventInput, ParticleType

    cfg = sim.cfg
    print("\n== v1 particle chain (stage-1 level) ==")
    # gamma containment/escape vs radius at 2 MeV
    for r in (0.0, 16.0, 17.5):
        esc = np.mean([
            run_s1(EventInput(r, 0, 0, 2.0, particle_type=ParticleType.GAMMA),
                   cfg, np.random.default_rng(10_000 + i)).e_escape_mev
            for i in range(n)
        ])
        print(f"gamma 2 MeV r={r:>4.1f} m: escape {esc / 2.0 * 100:6.3f}% "
              f"(0 at center; rises only within ~1 mfp of the wall)")
    # chain length and gamma flight-time spread
    ns_, ts_ = [], []
    for i in range(n):
        s1 = run_s1(EventInput(0, 0, 0, 1.46, particle_type=ParticleType.GAMMA),
                    cfg, np.random.default_rng(20_000 + i))
        ns_.append(s1.steps.n_steps)
        ts_.append(float(s1.steps.t_ns.max()))
    print(f"gamma 1.46 MeV center: mean steps {np.mean(ns_):.1f} (expect ~11), "
          f"chain time spread (max t) mean {np.mean(ts_):.2f} ns")
    # e+/e- visible-energy scale difference (effects.md #4 anchor: ~1%)
    e_vis_p = np.mean([
        run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON),
               cfg, np.random.default_rng(30_000 + i)).e_vis_mev
        for i in range(n)
    ])
    e_vis_e = run_s1(EventInput(0, 0, 0, 1.0), cfg).e_vis_mev
    scale = e_vis_p / e_vis_e / (1.0 + 1.021998)
    print(f"e+/e- visible scale @1 MeV kin: {scale:.4f} "
          f"(expect ~0.99; annihilation electrons quench more)")
    # o-Ps delayed component: P(first annih step t > thr) =
    # ops_fraction * exp(-thr/tau) (+small gamma flight-time contamination)
    t_ann = []
    for i in range(n):
        s1 = run_s1(EventInput(0, 0, 0, 1.0, particle_type=ParticleType.POSITRON),
                    cfg, np.random.default_rng(40_000 + i))
        m = s1.steps.kind >= 3
        t_ann.append(float(s1.steps.t_ns[m].min()))
    thr = 0.5
    frac = np.mean(np.asarray(t_ann) > thr)
    frac_exp = cfg.ops_fraction * np.exp(-thr / cfg.ops_tau_ns)
    print(f"o-Ps: P(first annih t > {thr} ns) = {frac:.3f} "
          f"(expect {frac_exp:.3f} = ops_frac*exp(-thr/tau); "
          f"delayed mean {np.mean([t for t in t_ann if t > thr]):.2f} ns "
          f"~ tau + flight = {cfg.ops_tau_ns})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", choices=["uniform", "juno"], default="uniform")
    args = ap.parse_args()

    t0 = time.time()
    layout = (
        PMTLayout.from_juno_csv()
        if args.layout == "juno"
        else PMTLayout.uniform()
    )
    print(f"layout={args.layout}: {layout.n_pmt} PMTs on R={layout.radius_m:.2f} m "
          f"({time.time()-t0:.2f}s)")
    sim = DetectorSim(DetectorConfig(), layout, seed=42)

    ev = sim.generate(0, 0, 0, 1.0)
    print(f"\n== anchor ==\ncenter 1 MeV: {ev.n_pe_total} pe "
          f"(mu_pe_per_mev_center={sim.cfg.mu_pe_per_mev_center})")

    # geometric coverage: N * pi(d/2)^2 / (4 pi R^2)
    cov = (layout.n_pmt * np.pi * (sim.cfg.pmt_diameter_m / 2) ** 2
           / (4 * np.pi * layout.radius_m ** 2))
    print(f"geometric coverage: {cov:.3f} (expect ~0.75)")

    # staged-chain consistency at the center
    n_ev = 200
    ng, npe = [], []
    for _ in range(n_ev):
        e = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
        ng.append(e.n_gamma); npe.append(e.n_pe_total)
    print(f"stage check: <n_gamma>={np.mean(ng):.0f} (expect {sim.cfg.ly_photons_mev}), "
          f"<n_pe>/<n_gamma>={np.mean(npe)/np.mean(ng):.4f} "
          f"(expect p_det={sim.cfg.p_det_center:.4f})")

    # charge-pattern pointing: near vs far PMT for an off-center vertex
    near_pe = []
    for _ in range(20):
        ev = sim.generate(15.0, 0, 0, 1.0, with_waveforms=False)
        d = np.linalg.norm(layout.positions_m - [15, 0, 0], axis=1)
        nearest = np.argmin(d)
        k = int(np.where(ev.pmt_ids == nearest)[0][0]) \
            if nearest in ev.pmt_ids else None
        near_pe.append(ev.n_pe_pmt[k] if k is not None else 0)
    print(f"off-center r=15m: n_pe~{ev.n_pe_total}, "
          f"nearest-PMT pe per event ~{np.mean(near_pe):.1f} "
          f"(expect ~1.5; charge pattern carries pointing info)")

    timing_check(sim)
    trigger_check(sim)
    particle_check(sim)
    energy_scan(sim, [1.0, 2.0, 5.0, 10.0], n_per=1000)
    radial_scan(sim)

    t0 = time.time()
    n_wf = 50
    for _ in range(n_wf):
        sim.generate(0, 0, 0, 1.0, with_waveforms=True)
    dt = (time.time() - t0) / n_wf
    print(f"\n== throughput ==\nwith waveforms: {dt*1000:.0f} ms/event "
          f"-> {n_wf/dt:.0f} events/min/core")


if __name__ == "__main__":
    main()
