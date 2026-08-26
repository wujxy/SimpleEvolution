"""Per-link chain figures: E_true -> N_gamma -> N_pe -> Q_PMT -> waveform.

    python3 scripts/make_chain_figures.py [--quick]

One PNG per chain link under figures/chain_*.png plus the single-event
anatomy figure. Statistics panels use the default fast optics (noted in
titles); single-event panels (PE map, anatomy) use trace optics to match
the frozen benchmark.
"""

import argparse
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
from benchmarks.JunoResBench.juno_res_bench.stages import (  # noqa: E402
    s1_response,
    s2_photons,
)
from benchmarks.JunoResBench.juno_res_bench.truth import EventInput  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench._vendor.wavegen_v1 import (  # noqa: E402
    WaveGenConfig,
)

FIGDIR = Path(__file__).resolve().parents[1] / "figures"


def _mollweide_ax():
    return plt.subplot(projection="mollweide")


def _pe_sky_map(ax, truth, layout, title):
    counts = np.zeros(layout.n_pmt)
    counts[truth.pmt_ids] = truth.n_pe_pmt
    pos = layout.positions_m
    r = np.linalg.norm(pos, axis=1)
    theta = np.arccos(np.clip(pos[:, 2] / r, -1, 1))
    phi = np.arctan2(pos[:, 1], pos[:, 0])
    # mollweide convention: longitude in [-pi, pi], latitude = pi/2 - theta
    lon = np.mod(phi + np.pi, 2 * np.pi) - np.pi
    lat = np.pi / 2 - theta
    m = counts > 0
    sc = ax.scatter(lon[m], lat[m], c=counts[m], s=2.5, cmap="viridis",
                    norm=matplotlib.colors.LogNorm())
    plt.colorbar(sc, ax=ax, shrink=0.6, label="PE per PMT")
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)


# ---- link 1: E_true -> E_dep -> E_vis ------------------------------------
def fig_s1_ratios(cfg):
    e = np.linspace(0.3, 10, 300)
    e_vis = np.array([cfg.quench(x) * cfg.nl_correction(x) for x in e])
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ax.plot(e, np.ones_like(e), ":", color="grey", label="E_dep / E_true (identity, e branch)")
    ax.plot(e, np.array([cfg.quench(x) for x in e]) / e,
            label="Birks quench (kB·dE/dx=0.0241)")
    ax.plot(e, np.array([cfg.nl_correction(x) for x in e]), label="low-E NL correction")
    ax.plot(e, e_vis / e, "k", lw=2, label="E_vis / E_true (product)")
    ax.set_ylim(0.9, 1.05)
    ax.set_xlabel("E_true [MeV]")
    ax.set_ylabel("ratio")
    ax.set_title("Stage 1 chain: deterministic, no fluctuation (e branch)\n"
                 "escape/quench dE/dx(E) effects land in v1 gamma branch", fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s1_etru.png", dpi=130)
    plt.close(fig)


# ---- link 2: E_vis -> N_gamma ---------------------------------------------
def fig_s2_pull(cfg, n_events=2000, e_mev=5.0):
    pulls, nc_frac = [], []
    for i in range(n_events):
        rngs_i = np.random.default_rng(1000 + i)
        ev = EventInput(0, 0, 0, e_mev)
        s1 = s1_response.run_s1(ev, cfg)
        ph = s2_photons.run_s2_scint(s1, ev, cfg, rngs_i)
        mu = s1.e_vis_mev * cfg.ly_photons_mev
        pulls.append((len(ph) - mu) / np.sqrt(mu))
        ph_c = s2_photons.run_s2_cherenkov(s1, ev, cfg, rngs_i)
        nc_frac.append(len(ph_c) / max(len(ph), 1))
    pulls = np.array(pulls)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].hist(pulls, bins=60, density=True)
    x = np.linspace(-5, 5, 200)
    axes[0].plot(x, np.exp(-x * x / 2) / np.sqrt(2 * np.pi), "r",
                 label=f"N(0,1),  meas std={pulls.std():.3f}")
    axes[0].set_xlabel(r"($N_\gamma-\mu)/\sqrt{\mu}$")
    axes[0].set_title(f"N_gamma fluctuation @ {e_mev} MeV (Poisson check)", fontsize=10)
    axes[0].legend(fontsize=9)
    axes[1].hist(nc_frac, bins=60, density=True, color="#d62728")
    axes[1].set_xlabel(r"$N_C / N_\gamma$")
    axes[1].set_title(f"Cherenkov fraction @ {e_mev} MeV "
                      f"(mean {np.mean(nc_frac)*100:.2f}%)", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s2_pull.png", dpi=130)
    plt.close(fig)


# ---- link 3: N_gamma -> N_pe ----------------------------------------------
def fig_s4_pe_map(sim_t, layout):
    truth = sim_t.generate(0, 0, 0, 5.0, with_waveforms=False)
    fig = plt.figure(figsize=(9, 4.6))
    _pe_sky_map(_mollweide_ax(), truth, layout,
                "one 5 MeV event @ center (trace): N_pe per PMT — "
                "nonuniformity + PDE spread texture")
    fig.savefig(FIGDIR / "chain_s4_pe_map.png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return truth


def fig_s4_npe_vs_e(sim_f, cfg, energies=(1, 2, 4, 6, 8), n_per=400):
    means, sigs = [], []
    for e0 in energies:
        pe = [sim_f.generate(0, 0, 0, float(e0), with_waveforms=False).n_pe_total
              for _ in range(n_per)]
        means.append(np.mean(pe))
        sigs.append(np.std(pe) / np.mean(pe))
    means, sigs = np.array(means), np.array(sigs)
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ax.errorbar(energies, means, yerr=means * sigs, fmt="o",
                label=r"$N_{pe}$ mean $\pm$ std")
    ax.set_xlabel("E_true [MeV]")
    ax.set_ylabel("N_pe total", color="C0")
    ax2 = ax.twinx()
    ref_poisson = 1 / np.sqrt(means)
    p_det = cfg.p_det_center
    ref_thin = np.sqrt((1 - p_det) / means)   # Bernoulli thinning, delta_i is
    # a static per-PMT offset: it does NOT broaden the event-wise total
    ax2.plot(energies, sigs, "s-", color="C3", label="measured")
    ax2.plot(energies, ref_poisson, "--", color="grey",
             label=r"$1/\sqrt{N_{pe}}$ (Poisson $N_\gamma$)")
    ax2.plot(energies, ref_thin, "-.", color="C1",
             label=r"$\sqrt{(1-p)/N_{pe}}$ (+detection thinning)")
    ax2.set_ylabel(r"$\sigma(N_{pe})/\mu$", color="C3")
    ax2.legend(fontsize=8, loc="upper right")
    ax.set_title("N_pe linearity + resolution (fast optics, center)", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s4_npe_vs_e.png", dpi=130)
    plt.close(fig)


def fig_s4_pmt_hist(truth, cfg):
    n = truth.n_pe_pmt.astype(float)
    mu_bar = truth.n_pe_total / len(n)
    fig, ax = plt.subplots(figsize=(7, 4.4))
    bins = np.arange(-0.5, n.max() + 1.5)
    ax.hist(n, bins=bins, density=True, alpha=0.7,
            label=f"per-PMT counts, {len(n)} hit PMTs")
    k = np.arange(0, int(n.max()) + 1)
    pmf = np.exp(-mu_bar) * mu_bar ** k / \
        np.array([np.math.factorial(int(v)) for v in k])
    ax.plot(k, pmf, "r-", label=f"Poisson({mu_bar:.1f}) reference")
    ax.set_yscale("log")
    ax.set_xlabel("n_pe in one PMT")
    ax.set_ylabel("density")
    ax.set_title("one event: per-PMT PE counts — broader than Poisson "
                 "(mu_i spread + delta_i)", fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s4_pmt_hist.png", dpi=130)
    plt.close(fig)


def fig_s4_efficiency(energies=(1, 5), radii=(0, 5, 10, 15), n_per=200):
    fig, ax = plt.subplots(figsize=(7, 4.4))
    for e0, color in zip(energies, ("C0", "C1")):
        for label, mk in (("fast", "o"), ("trace", "s")):
            sim = DetectorSim(DetectorConfig(optics_mode=mk),
                              PMTLayout.uniform(), seed=41)
            rs, eff = [], []
            for r0 in radii:
                vals = []
                for _ in range(n_per):
                    t = sim.generate(r0, 0, 0, float(e0), with_waveforms=False)
                    vals.append(t.n_pe_total / max(t.n_gamma, 1))
                rs.append(r0)
                eff.append(np.mean(vals))
            ax.plot(rs, eff, mk + "-", color=color, alpha=1.0 if mk == "trace" else 0.45,
                    label=f"{e0} MeV {label}")
    ax.set_xlabel("vertex radius r [m]")
    ax.set_ylabel(r"$N_{pe}/N_\gamma$ (detection efficiency)")
    ax.set_title("realized detection efficiency: fast (folded) vs trace "
                 "(ESR recycling visible)", fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s4_efficiency.png", dpi=130)
    plt.close(fig)


# ---- link 4: N_pe -> Q_PMT -------------------------------------------------
def fig_s5_spe(sim_f, n=200000):
    q1 = sim_f.wavegen._sample_amplitudes(n, 1.0)
    q2 = q1[: n // 2] + q1[n // 2:]
    q3 = q1[: n // 4] + q1[n // 4: n // 2] + q1[n // 2: 3 * n // 4]
    fig, ax = plt.subplots(figsize=(7, 4.4))
    b = np.linspace(0, 6, 240)
    ax.hist(q1, bins=b, density=True, alpha=0.6, label="1 pe")
    ax.hist(q2, bins=b, density=True, alpha=0.6, label="2 pe (sum)")
    ax.hist(q3, bins=b, density=True, alpha=0.6, label="3 pe (sum)")
    ax.set_yscale("log")
    ax.set_xlabel("sampled SPE charge [pe]")
    ax.set_ylabel("density")
    ax.set_title("SPE charge spectrum (Gaussian core + 10% exp tail, "
                 "waverec snapshot)", fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_spe.png", dpi=130)
    plt.close(fig)


def _channel_charges(truth):
    base = sim_baseline()
    out = []
    for k in range(len(truth.pmt_ids)):
        i0, i1 = truth.pe_offsets[k], truth.pe_offsets[k + 1]
        out.append((base - truth.adc[k].astype(float)).sum())
    return np.array(out)


_BASE = {}


def sim_baseline():
    cfg = WaveGenConfig()
    if "b" not in _BASE:
        _BASE["b"] = cfg.baseline_adc
    return _BASE["b"]


def fig_s5_q_vs_npe(sim_w, n_events=60):
    qs, nps = [], []
    for _ in range(n_events):
        t = sim_w.generate(0, 0, 0, 5.0, with_waveforms=True)
        q = _channel_charges(t)
        qs.append(q)
        nps.append(t.n_pe_pmt.astype(float))
    q = np.concatenate(qs)
    np_ = np.concatenate(nps)
    fig, ax = plt.subplots(figsize=(7, 4.6))
    ax.scatter(np_ + np.random.default_rng(0).uniform(-0.2, 0.2, len(np_)),
               q / 1e3, s=5, alpha=0.25)
    lev = np.unique(np_)
    mean = [q[np_ == v].mean() / 1e3 for v in lev]
    std = [q[np_ == v].std() / 1e3 for v in lev]
    ax.errorbar(lev, mean, yerr=std, fmt="r.-", label="mean$\\pm$std")
    ax.set_xlabel("true in-window N_pe (channel)")
    ax.set_ylabel("integrated charge [k ADC counts]")
    ax2 = ax.twinx()
    res = np.array(std) / np.array(mean)
    ax2.plot(lev, res, ":", color="C2", label=r"$\sigma_Q/Q$")
    ax2.set_ylabel(r"$\sigma_Q/Q$", color="C2")
    ax2.legend(fontsize=8, loc="lower right")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("channel charge vs true PE count (5 MeV events, gain 15%)",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_q_vs_npe.png", dpi=130)
    plt.close(fig)


def fig_s5_gain(sim_on, sim_off, n_events=40, npe_sel=2):
    def collect(sim):
        out = []
        for _ in range(n_events):
            t = sim.generate(0, 0, 0, 5.0, with_waveforms=True)
            m = t.n_pe_pmt == npe_sel
            if m.any():
                q = _channel_charges(t)
                out.append(q[m] / npe_sel)
        return np.concatenate(out)

    q_on, q_off = collect(sim_on), collect(sim_off)
    if len(q_on) == 0 or len(q_off) == 0:
        raise RuntimeError("no channels matched n_pe selection")
    q_on, q_off = q_on / np.mean(q_on), q_off / np.mean(q_off)
    fig, ax = plt.subplots(figsize=(7, 4.4))
    hi = 1.3 * max(np.percentile(q_on, 99.5), np.percentile(q_off, 99.5))
    b = np.linspace(0, hi, 80)
    ax.hist(q_off, bins=b, density=True, alpha=0.6,
            label=f"gain spread 0%: rel std {np.std(q_off):.3f}")
    ax.hist(q_on, bins=b, density=True, alpha=0.6,
            label=f"gain spread 15%: rel std {np.std(q_on):.3f}")
    ax.set_xlabel(f"charge per PE (channels with n_pe={npe_sel}), normalized")
    ax.set_ylabel("density")
    ax.set_title("per-PMT gain spread broadens the channel charge response",
                 fontsize=10)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_gain.png", dpi=130)
    plt.close(fig)


# ---- link 5: Q_PMT -> waveform --------------------------------------------
def fig_s5_waveforms(sim_w, truth=None):
    if truth is None:
        truth = sim_w.generate(0, 0, 0, 5.0, with_waveforms=True)
    n = truth.n_pe_pmt
    picks = []
    for target in (1, 5, 20):
        cand = np.where(n == target)[0]
        if len(cand):
            picks.append(int(cand[0]))
        else:
            picks.append(int(np.argmin(np.abs(n - target))))
    fig, axes = plt.subplots(len(picks), 1, figsize=(9, 2.4 * len(picks)),
                             sharex=True)
    for ax, k in zip(axes, picks):
        adc = truth.adc[k].astype(float)
        base = sim_baseline()
        sig = base - adc
        ts = np.arange(len(adc))
        ax.plot(ts, sig, lw=0.7)
        i0, i1 = truth.pe_offsets[k], truth.pe_offsets[k + 1]
        for tpe in truth.t_rel_ns[i0:i1]:
            ax.axvline(tpe, color="r", alpha=0.3, lw=0.7)
        ax.set_ylabel(f"baseline$-$ADC\n(n_pe={n[k]})")
        ax.set_xlim(0, 1000)
    axes[-1].set_xlabel("time in window [ns]  (red: true PE times)")
    fig.suptitle("example waveforms: SPE pulses + shaping + noise", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIGDIR / "chain_s5_waveforms.png", dpi=130)
    plt.close(fig)


def fig_s5_timing(sim_w, n_events=30, thresh_counts=25):
    base = sim_baseline()
    diffs = []
    for _ in range(n_events):
        t = sim_w.generate(0, 0, 0, 5.0, with_waveforms=True)
        for k in range(len(t.pmt_ids)):
            i0, i1 = t.pe_offsets[k], t.pe_offsets[k + 1]
            t_first = float(np.min(t.t_rel_ns[i0:i1]))
            sig = base - t.adc[k].astype(float)
            above = np.where(sig > thresh_counts)[0]
            if len(above) == 0:
                continue
            diffs.append(float(above[0]) - t_first)
    diffs = np.array(diffs)
    fig, ax = plt.subplots(figsize=(7, 4.4))
    ax.hist(diffs, bins=120)
    ax.axvline(0, color="r", ls="--")
    ax.set_yscale("log")
    ax.set_xlim(-150, 150)
    ax.set_xlabel("leading-edge time $-$ earliest true PE [ns]")
    ax.set_ylabel("channels")
    ax.set_title(f"waveform timing fidelity (fixed threshold, {n_events} events)\n"
                 f"median {np.median(diffs):+.1f} ns (pulse-rise offset), "
                 f"core rms {diffs[np.abs(diffs) < 50].std():.1f} ns; "
                 "left tail = dark pulses before first PE", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_timing.png", dpi=130)
    plt.close(fig)


def fig_s5_dark_ap(sim_on, sim_off, n_events=40):
    def one_pe_charges(sim):
        out = []
        for _ in range(n_events):
            t = sim.generate(0, 0, 0, 1.0, with_waveforms=True)
            m = t.n_pe_pmt == 1
            if m.any():
                q = _channel_charges(t)
                out.append(q[m])
        return np.concatenate(out)

    q_on, q_off = one_pe_charges(sim_on), one_pe_charges(sim_off)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    b = np.linspace(0, np.percentile(np.concatenate([q_on, q_off]), 99.5) * 1.4, 90)
    axes[0].hist(q_off, bins=b, density=True, alpha=0.6, label="afterpulse OFF")
    axes[0].hist(q_on, bins=b, density=True, alpha=0.6,
                 label="afterpulse ON (p=1.6%, tau=500ns)")
    axes[0].set_xlabel("1-PE channel charge [ADC counts]")
    axes[0].set_title("afterpulse charge tail", fontsize=10)
    axes[0].legend(fontsize=9)
    # dark-only waveforms
    cfg = WaveGenConfig()
    rng = np.random.default_rng(5)
    dark_mean = 24000 * cfg.n_samples * cfg.sample_interval_ns * 1e-9
    shown = 0
    while shown < 5:
        n_dark = int(rng.poisson(dark_mean))
        if n_dark == 0:
            continue
        times = rng.uniform(0, cfg.n_samples * cfg.sample_interval_ns, n_dark)
        amps = sim_on.wavegen._sample_amplitudes(n_dark, 1.0)
        adc = sim_on.wavegen._synthesize(times, amps)
        axes[1].plot(np.arange(len(adc)), adc - cfg.baseline_adc, lw=0.7,
                     alpha=0.8, label=f"{n_dark} dark PE" if shown < 2 else None)
        shown += 1
    axes[1].set_xlabel("time [ns]")
    axes[1].set_ylabel("baseline$-$ADC")
    axes[1].set_title("dark-noise-only channels (24 kHz, ~0.02 PE/window)",
                      fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_dark_ap.png", dpi=130)
    plt.close(fig)


def fig_s5_window(sim_f, n_events=300, e_mev=5.0):
    t_hits = []
    for _ in range(n_events):
        t = sim_f.generate(0, 0, 0, e_mev, with_waveforms=False)
        t_hits.append(np.asarray(t.t_rel_ns, float) - 300.0)   # back to t_hit
    t_hits = np.concatenate(t_hits)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    axes[0].hist(t_hits, bins=120)
    axes[0].axvspan(-300, 700, alpha=0.15, color="g", label="readout window")
    axes[0].set_xlabel("PE hit time rel. t0 [ns]")
    axes[0].set_yscale("log")
    axes[0].legend(fontsize=9)
    axes[0].set_title("PE time distribution vs 1 us window", fontsize=10)
    shifts = np.linspace(-400, 400, 81)
    frac = [np.mean((t_hits + 300 + d >= 0) & (t_hits + 300 + d < 1000))
            for d in shifts]
    axes[1].plot(shifts, frac)
    axes[1].set_xlabel("t0 mis-modeling [ns]")
    axes[1].set_ylabel("PE fraction inside window")
    axes[1].set_title("window truncation vs trigger-time offset", fontsize=10)
    fig.tight_layout()
    fig.savefig(FIGDIR / "chain_s5_window.png", dpi=130)
    plt.close(fig)


# ---- anatomy ---------------------------------------------------------------
def fig_anatomy(sim_t, truth, layout):
    fig = plt.figure(figsize=(12, 9))
    ax1 = _pe_sky_map(plt.subplot(2, 2, 1, projection="mollweide"), truth,
                      layout, "N_pe per PMT (sky map)")
    ax2 = plt.subplot(2, 2, 2)
    t_hit = np.asarray(truth.t_rel_ns, float) - 300.0
    ax2.hist(t_hit, bins=80)
    ax2.set_xlabel("PE hit time rel. t0 [ns]")
    ax2.set_title(f"hit times, n_pe_total={truth.n_pe_total}", fontsize=10)
    ax3 = plt.subplot(2, 2, 3)
    pos = layout.positions_m[truth.pmt_ids]
    cos_th = pos[:, 2] / np.linalg.norm(pos, axis=1)
    ax3.scatter(cos_th, truth.n_pe_pmt, s=5, alpha=0.4)
    ax3.set_xlabel("cos(polar angle of PMT)")
    ax3.set_ylabel("n_pe per PMT")
    ax3.set_title("per-PMT occupancy vs polar angle", fontsize=10)
    ax4 = plt.subplot(2, 2, 4)
    k = int(np.argmax(truth.n_pe_pmt))
    adc = truth.adc[k].astype(float)
    ax4.plot(np.arange(len(adc)), sim_baseline() - adc, lw=0.7)
    i0, i1 = truth.pe_offsets[k], truth.pe_offsets[k + 1]
    for tpe in truth.t_rel_ns[i0:i1]:
        ax4.axvline(tpe, color="r", alpha=0.25, lw=0.6)
    ax4.set_xlim(0, 1000)
    ax4.set_xlabel("time in window [ns]")
    ax4.set_title(f"brightest channel waveform (n_pe={truth.n_pe_pmt[k]})",
                  fontsize=10)
    fig.suptitle("one-event anatomy: 5 MeV electron @ center, trace optics",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(FIGDIR / "chain_anatomy.png", dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="fewer events (smoke test)")
    args = ap.parse_args()
    FIGDIR.mkdir(parents=True, exist_ok=True)
    n_scale = 0.25 if args.quick else 1.0

    cfg = DetectorConfig()                      # fast optics, stage-5 physics
    cfg_t = DetectorConfig(optics_mode="trace")
    lay = PMTLayout.uniform()
    sim_f = DetectorSim(cfg, lay, seed=101)     # fast, no waveforms needed
    sim_w = DetectorSim(cfg, lay, seed=102)     # fast, with waveforms
    sim_t = DetectorSim(cfg_t, lay, seed=103)   # trace
    sim_g0 = DetectorSim(cfg, lay, seed=102,
                         wave_config=WaveGenConfig(gain_spread=0.0))
    sim_ap0 = DetectorSim(DetectorConfig(afterpulse_prob=0.0), lay, seed=102)

    print("link 1: E->E_vis ratios")
    fig_s1_ratios(cfg)
    print("link 2: N_gamma pull")
    fig_s2_pull(cfg, n_events=int(2000 * n_scale))
    print("link 3: PE sky map (trace)")
    truth_t = fig_s4_pe_map(sim_t, lay)
    print("link 3: N_pe vs E")
    fig_s4_npe_vs_e(sim_f, cfg, n_per=int(400 * n_scale))
    print("link 3: per-PMT hist")
    fig_s4_pmt_hist(truth_t, cfg)
    print("link 3: efficiency fast vs trace")
    fig_s4_efficiency(n_per=int(200 * n_scale))
    print("link 4: SPE spectrum")
    fig_s5_spe(sim_w)
    print("link 4: charge vs N_pe")
    fig_s5_q_vs_npe(sim_w, n_events=int(60 * n_scale))
    print("link 4: gain spread")
    fig_s5_gain(sim_w, sim_g0, n_events=int(40 * n_scale))
    print("link 5: waveforms")
    truth_w = sim_w.generate(0, 0, 0, 5.0, with_waveforms=True)
    fig_s5_waveforms(sim_w, truth_w)
    print("link 5: timing fidelity")
    fig_s5_timing(sim_w, n_events=int(30 * n_scale))
    print("link 5: dark + afterpulse")
    fig_s5_dark_ap(sim_w, sim_ap0, n_events=int(40 * n_scale))
    print("link 5: window truncation")
    fig_s5_window(sim_f, n_events=int(300 * n_scale))
    print("anatomy")
    truth_a = sim_t.generate(0, 0, 0, 5.0, with_waveforms=True)
    fig_anatomy(sim_t, truth_a, lay)
    print("done ->", FIGDIR)


if __name__ == "__main__":
    main()
