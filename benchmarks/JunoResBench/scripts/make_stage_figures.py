"""Per-stage intermediate figures, generated directly from DetectorSim.

    python3 scripts/make_stage_figures.py [--out-dir figures]

Produces figures/stage{1..5}.png — one figure per forward-model stage,
each panel illustrating one effect (see docs/effects.md for numbering).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.stages.s1_response import nl_corr  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.stages.s2_photons import (  # noqa: E402
    beta_from_kinetic,
)
from benchmarks.JunoResBench.juno_res_bench.stages.s3_optics import (  # noqa: E402
    scint_weights,
)
from benchmarks.JunoResBench.juno_res_bench.stages.s4_detection import (  # noqa: E402
    ce_factor,
)


def stage1(cfg, out):
    """E_true -> E_vis: Birks + low-energy nonlinearity (B3/B7)."""
    e = np.geomspace(0.1, 20, 200)
    vis = e / (1 + cfg.birks_kB_ddx) * np.array([nl_corr(x, cfg) for x in e])
    fig, ax = plt.subplots(figsize=(6, 4.2))
    ax.plot(e, vis / e, "b-", lw=2)
    ax.axhline(1 / (1 + cfg.birks_kB_ddx), color="r", ls="--", lw=1,
               label=f"Birks only ({1/(1+cfg.birks_kB_ddx):.4f})")
    ax.set_xscale("log")
    ax.set_xlabel("E_true [MeV]")
    ax.set_ylabel("E_vis / E_true")
    ax.set_title("Stage 1: quenching + low-E nonlinearity")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "stage1.png", dpi=130)
    plt.close(fig)


def stage2(cfg, out):
    """Photon counts + timing + Cherenkov cone (B1/B2/B4/B5)."""
    lay = PMTLayout.uniform()
    rng = np.random.default_rng(2)
    # N_gamma fluctuation at 1 MeV
    ng = [DetectorSim(cfg, lay, seed=s).generate(0, 0, 0, 1.0,
         with_waveforms=False).n_gamma for s in range(300)]
    # emission-time components
    ev = DetectorSim(cfg, lay, seed=1).generate(0, 0, 0, 1.0, with_waveforms=False)
    # Cherenkov cone (3 MeV, fixed direction)
    track = np.array([1.0, -1.0, 2.0]) / np.sqrt(6.0)
    dirs = []
    for _ in range(40):
        e3 = DetectorSim(cfg, lay, seed=int(rng.integers(1e6))).generate(
            0, 0, 0, 3.0, with_waveforms=False, direction=tuple(track))
        # cone visible in photon dirs only inside stage 2; use PE PMT dirs
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))
    axes[0].hist(ng, bins=40)
    axes[0].set_title(f"N_gamma @1MeV: <{np.mean(ng):.0f}>, "
                      f"sigma {np.std(ng):.0f} (~sqrt={np.sqrt(cfg.ly_photons_mev*cfg.quench(1)*cfg.nl_correction(1)):.0f})")
    axes[1].hist(ev.t_emit_ns, bins=100, range=(0, 150))
    for tau, w in cfg.scint_taus_ns:
        axes[1].axvline(tau, color="r", ls=":", lw=0.8)
    axes[1].set_yscale("log")
    axes[1].set_title("scint emission time (red: 4 components)")
    # Cherenkov fraction vs E
    es = np.linspace(0.3, 10, 40)
    frac = []
    for e in es:
        beta = beta_from_kinetic(e)
        f_c = max(1 - 1 / (cfg.ls_refractive_index * beta) ** 2, 0)
        frac.append(cfg.ly_cherenkov * f_c / (cfg.ly_photons_mev * cfg.quench(1)))
    axes[2].plot(es, np.array(frac) * 100)
    axes[2].set_xlabel("E [MeV]"); axes[2].set_ylabel("N_C / N_scint [%]")
    axes[2].set_title("Cherenkov fraction (threshold 0.17 MeV)")
    fig.tight_layout()
    fig.savefig(out / "stage2.png", dpi=130)
    plt.close(fig)


def stage3(cfg, out):
    """Optics: geometric weight pattern + Cherenkov arrival + scatter (C2-C5)."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(cfg, lay, seed=3)
    # weight pattern for off-center vertex
    vtx = np.array([15.0, 0, 0])
    w = scint_weights(lay, vtx, cfg)
    ang_pmt = np.degrees(np.arccos(lay.positions_m[:, 0] / lay.radius_m))
    # Cherenkov arrival angular structure
    track = np.array([1.0, -1.0, 2.0]) / np.sqrt(6.0)
    theta_c = np.degrees(np.arccos(1 / (cfg.ls_refractive_index
                                        * beta_from_kinetic(3.0))))
    ang_cher = []
    for _ in range(30):
        ev = sim.generate(0, 0, 0, 3.0, with_waveforms=False,
                          direction=tuple(track))
        pe_pmt = np.repeat(ev.pmt_ids, np.diff(ev.pe_offsets))
        pd = lay.positions_m[pe_pmt] / np.linalg.norm(
            lay.positions_m[pe_pmt], axis=1, keepdims=True)
        m = ev.pe_type == 1
        ang_cher.append(np.degrees(np.arccos(np.clip(pd[m] @ track, -1, 1))))
    ang_cher = np.concatenate(ang_cher)
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))
    sc = axes[0].scatter(ang_pmt, w * 1e4, s=4, c=lay.positions_m[:, 2])
    axes[0].set_xlabel("angle from vertex direction [deg]")
    axes[0].set_ylabel(r"weight $w_i$ [$10^{-4}$]")
    axes[0].set_title("A_proj/d$^2$ weights, vertex r=15 m (near bright)")
    axes[1].hist(ang_cher, bins=60, range=(0, 120))
    axes[1].axvline(theta_c, color="r", ls="--",
                    label=fr"$\theta_C={theta_c:.1f}^\circ$")
    axes[1].set_xlabel("PMT angle from track [deg]")
    axes[1].set_title("Cherenkov arrivals carry the cone")
    axes[1].legend()
    # scatter spread vs path length (analytic + sampled residual)
    d = np.linspace(1, 38, 50)
    axes[2].plot(d, cfg.a_scatter_ns_per_m * d, "b-",
                 label=f"sigma = {cfg.a_scatter_ns_per_m} ns/m $\\times$ d")
    res = []
    for _ in range(100):
        e1 = sim.generate(0, 0, 0, 1.0, with_waveforms=False)
        res.append(e1.t_rel_ns - e1.t_emit_ns - e1.t_tof_ns - cfg.pre_trigger_ns)
    axes[2].axhline(np.sqrt(cfg.tts_sigma_ns**2
                            + (cfg.a_scatter_ns_per_m * 19)**2),
                    color="r", ls="--", label="total @center path")
    axes[2].set_xlabel("path [m]"); axes[2].set_ylabel("time spread [ns]")
    axes[2].set_title("C2/C3 scatter timing spread")
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(out / "stage3.png", dpi=130)
    plt.close(fig)


def stage4(cfg, out):
    """Detection: CE(theta) suppression + PDE recovery (D2/D3)."""
    lay = PMTLayout.uniform()
    sim = DetectorSim(cfg, lay, seed=4)
    # incidence angle vs detected pe for one off-center event
    ev = sim.generate(15.0, 0, 0, 3.0, with_waveforms=False)
    chord = lay.positions_m[ev.pmt_ids] - np.array([15.0, 0, 0])
    chord /= np.linalg.norm(chord, axis=1, keepdims=True)
    cos_inc = -np.einsum("ij,ij->i", chord, lay.inward_normals[ev.pmt_ids])
    inc = np.degrees(np.arccos(np.clip(cos_inc, -1, 1)))
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))
    axes[0].scatter(inc, ev.n_pe_pmt, s=8, alpha=0.6)
    th = np.linspace(0, 90, 100)
    for a, al in ((0.5, 0.5), (1.5, 0.7), (3.0, 0.9)):
        axes[0].plot(th, a * ce_factor(cfg, np.cos(np.radians(th)))
                     / ce_factor(cfg, np.array([1.0])), "r--", lw=0.8, alpha=al)
    axes[0].set_xlabel("incidence angle [deg]"); axes[0].set_ylabel("pe per PMT")
    axes[0].set_title("CE(theta) suppression (red: 0.5/1.5/3 pe scaled)")
    # PDE recovery on a small layout
    cfg_big = DetectorConfig(pde_sigma=0.30)
    lay_s = PMTLayout.uniform(400, 19.365)
    sim_s = DetectorSim(cfg_big, lay_s, seed=5)
    rng = np.random.default_rng(9)
    counts = np.zeros(lay_s.n_pmt)
    for _ in range(2000):
        u = 16 * rng.random() ** (1 / 3)
        ct = rng.uniform(-1, 1); st = np.sqrt(1 - ct * ct)
        phi = rng.uniform(0, 2 * np.pi)
        e = sim_s.generate(*(u * st * np.cos(phi), u * st * np.sin(phi), u * ct),
                           4.0, with_waveforms=False)
        counts[e.pmt_ids] += e.n_pe_pmt
    excess = (counts - counts.mean()) / np.sqrt(np.maximum(counts, 1))
    corr = np.corrcoef(excess, sim_s.calib.pde_delta)[0, 1]
    axes[1].scatter(sim_s.calib.pde_delta, excess, s=8, alpha=0.6)
    axes[1].set_xlabel(r"true $pde\_delta_i$ (sigma=0.3)")
    axes[1].set_ylabel("standardized excess counts")
    axes[1].set_title(f"per-PMT PDE recoverable: corr={corr:.2f}")
    # yield anchored
    pe = [sim.generate(0, 0, 0, 1.0, with_waveforms=False).n_pe_total
          for _ in range(200)]
    axes[2].hist(pe, bins=30)
    axes[2].set_title(f"total pe @1 MeV center: {np.mean(pe):.0f}")
    fig.tight_layout()
    fig.savefig(out / "stage4.png", dpi=130)
    plt.close(fig)


def _count_pulses(adc, cfg, wave_cfg):
    base = int(round(wave_cfg.baseline_frac * ((1 << wave_cfg.adc_bits) - 1)))
    sigma = wave_cfg.noise_sigma_mv * 1e-3 / wave_cfg.lsb_v
    below = adc < base - 5 * sigma
    return int(np.sum(below[1:] & ~below[:-1]))


def stage5(cfg, out):
    """Electronics: waveform with AP/dark + time offsets (E2/E3/E4)."""
    from benchmarks.JunoResBench.juno_res_bench._vendor.wavegen_v1 import (
        WaveGenConfig,
    )
    wave_cfg = WaveGenConfig()
    lay = PMTLayout.uniform()
    sim_off = DetectorSim(DetectorConfig(afterpulse_prob=0.0, dark_rate_hz=0.0),
                          lay, seed=6)
    sim_on = DetectorSim(cfg, lay, seed=6)
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))
    for k in range(3):
        e0 = sim_off.generate(0, 0, 0, 1.0)
        e1 = sim_on.generate(0, 0, 0, 1.0)
        t = np.arange(1000)
        axes[0].plot(t, e0.adc[k], lw=0.7, alpha=0.6)
        axes[1].plot(t, e1.adc[k], lw=0.7, alpha=0.6)
    axes[0].set_title("clean: PE pulses only")
    axes[1].set_title("default: + dark + afterpulses")
    for ax in axes[:2]:
        ax.set_xlabel("t [ns]"); ax.set_ylabel("ADC")
    # time offset effect on residual
    res0, res1 = [], []
    for _ in range(100):
        a = DetectorSim(DetectorConfig(time_offset_sigma_ns=0.0), lay,
                        seed=7).generate(0, 0, 0, 1.0, with_waveforms=False)
        b = DetectorSim(DetectorConfig(time_offset_sigma_ns=3.0), lay,
                        seed=7).generate(0, 0, 0, 1.0, with_waveforms=False)
        res0 += list(a.t_rel_ns - a.t_emit_ns - a.t_tof_ns - cfg.pre_trigger_ns)
        res1 += list(b.t_rel_ns - b.t_emit_ns - b.t_tof_ns - cfg.pre_trigger_ns)
    axes[2].hist(res0, bins=80, alpha=0.5, label=f"no offset ({np.std(res0):.2f} ns)")
    axes[2].hist(res1, bins=80, alpha=0.5, label=f"sigma=3 ns ({np.std(res1):.2f} ns)")
    axes[2].set_yscale("log")
    axes[2].set_xlabel("t_rel - t_emit - tof - pre [ns]")
    axes[2].set_title("per-PMT time offsets (E2)")
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(out / "stage5.png", dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    out = Path(args.out_dir or Path(__file__).resolve().parents[1] / "figures")
    out.mkdir(parents=True, exist_ok=True)
    cfg = DetectorConfig()
    for fn in (stage1, stage2, stage3, stage4, stage5):
        t0 = __import__("time").time()
        fn(cfg, out)
        print(f"{fn.__name__} -> {out}/{fn.__name__}.png "
              f"({__import__('time').time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
