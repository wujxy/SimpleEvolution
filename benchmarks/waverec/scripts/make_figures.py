#!/usr/bin/env python3
"""Pedagogical figures for the waverec forward model.

Produces three PNGs under figures/:
  fig1_spe_and_pulse.png   the single photoelectron: charge spectrum + pulse shape
  fig2_forward_model.png   truth -> analog pulse train -> digitized waveform, step by step
  fig3_pileup.png          why reconstruction is hard: pile-up in real dataset events

Everything is drawn from wavegen itself (plus the frozen nominal dataset),
so the figures always agree with the generator.
"""

import json
import pathlib
import sys

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wavegen import PulseShape, WaveGenConfig, WaveformGenerator  # noqa: E402

# ---- palette (light mode) -------------------------------------------------
BLUE = "#2a78d6"    # series 1: primary data (waveforms)
ORANGE = "#eb6834"  # series 2: ground truth
AQUA = "#1baf7a"    # series 3: secondary decomposition
RED = "#d03b3b"     # status: missed / spurious
INK = "#0b0b0b"
SEC = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURF = "#fcfcfb"
PAGE = "#f9f9f7"

plt.rcParams.update(
    {
        "font.size": 9,
        "text.color": INK,
        "axes.labelcolor": SEC,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.titlesize": 9.5,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "figure.facecolor": PAGE,
        "axes.facecolor": SURF,
        "savefig.facecolor": PAGE,
    }
)

FIGDIR = ROOT / "figures"


def style_ax(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def load_template(cfg):
    g = WaveformGenerator(cfg, seed=0)
    tmpl = g._template
    peak_i = int(np.argmax(tmpl))
    t_rel = (np.arange(tmpl.size) - peak_i) * cfg.sample_interval_ns
    return tmpl, peak_i, t_rel


def place_pulse(tmpl, peak_i, t_hit_ns, amp, n_samples, dt_ns=1.0):
    """Replicate the generator's template placement for one pulse."""
    wf = np.zeros(n_samples)
    i0 = int(round(t_hit_ns / dt_ns)) - peak_i
    i1 = i0 + tmpl.size
    c0, c1 = max(i0, 0), min(i1, n_samples)
    if c0 < c1:
        wf[c0:c1] += amp * tmpl[c0 - i0 : c1 - i0]
    return wf


# ---------------------------------------------------------------------------
# figure 1 — the single photoelectron
# ---------------------------------------------------------------------------
def fig1(cfg):
    p = cfg.spe

    # (a) SPE charge spectrum: sample exactly like the generator does
    g = WaveformGenerator(cfg, seed=123)
    amp = g._sample_amplitudes(60000, channel_gain=1.0)

    x = np.linspace(0.001, 12.0, 1200)
    core = np.exp(-((x - p.gain) ** 2) / (2 * p.sigma_gain**2)) / (
        p.sigma_gain * np.sqrt(2 * np.pi)
    )
    tail = np.where(x >= p.tail_cutoff, np.exp(-(x - p.tail_cutoff) / p.tail_decay) / p.tail_decay, 0.0)
    mix = (1 - p.p_tail) * core + p.p_tail * tail

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(9.4, 3.9), constrained_layout=True)
    fig.suptitle("The single photoelectron (SPE): random charge, fixed shape", fontsize=11, fontweight="bold", x=0.005, ha="left")

    ax.hist(amp, bins=260, range=(0, 12), density=True, histtype="stepfilled",
            color=BLUE, alpha=0.15, edgecolor=BLUE, linewidth=1.4,
            label="generator samples")
    ax.plot(x, mix, color=INK, lw=1.2, label="model")
    ax.plot(x, (1 - p.p_tail) * core, color=AQUA, lw=1.6, label="Gaussian core (90%)")
    ax.plot(x, p.p_tail * tail, color=ORANGE, lw=1.6, label="exponential tail (10%)")
    ax.set_yscale("log")
    ax.set_ylim(3e-4, 3)
    ax.set_xlim(0, 12)
    style_ax(ax)
    ax.set_xlabel("charge of one PE [pe]")
    ax.set_ylabel("probability density [1/pe]")
    ax.set_title("(a) SPE charge spectrum — every PE carries a different charge", loc="left")
    ax.legend(loc="upper right")
    ax.annotate("one PE usually reads ~1 pe,\nbut the tail reaches several pe",
                xy=(3.6, 0.03), xytext=(6.4, 0.008), color=SEC,
                arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))

    # (b) pulse shape
    tmpl, peak_i, t_rel = load_template(cfg)
    mv = tmpl * 1000.0
    half = mv.max() / 2
    above = mv >= half
    i_lo, i_hi = np.argmax(above), len(mv) - 1 - np.argmax(above[::-1])
    fwhm = t_rel[i_hi] - t_rel[i_lo]

    ax2.fill_between(t_rel, mv.min(), mv.max(), where=np.zeros_like(mv, bool))
    ax2.axhspan(-cfg.noise_sigma_mv, cfg.noise_sigma_mv, color=MUTED, alpha=0.15, lw=0)
    ax2.plot(t_rel, mv, color=BLUE, lw=2.0, label="1-pe pulse (log-normal)")
    ax2.axhline(0, color=AXIS, lw=0.8)
    ax2.annotate(f"peak 7 mV\n({mv.max()/ (cfg.noise_sigma_mv):.0f}x the noise floor)",
                 xy=(0, mv.max()), xytext=(-140, mv.max() * 0.72), color=SEC,
                 arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    ax2.annotate(f"FWHM ≈ {fwhm:.0f} ns", xy=(t_rel[i_hi], half),
                 xytext=(t_rel[i_hi] + 22, half + 1.2), color=SEC,
                 arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    ax2.text(-142, cfg.noise_sigma_mv + 0.25, "electronics noise ±0.35 mV", color=MUTED, fontsize=7.5)
    ax2.set_xlim(-150, 150)
    ax2.set_ylim(-3, 8.4)
    style_ax(ax2)
    ax2.set_xlabel("time relative to pulse peak [ns]")
    ax2.set_ylabel("pulse height [mV]")
    ax2.set_title("(b) single-PE pulse shape — amplitude ∝ charge, shape fixed", loc="left")
    ax2.legend(loc="lower right")

    out = FIGDIR / "fig1_spe_and_pulse.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}")


# ---------------------------------------------------------------------------
# figure 2 — the forward model, step by step
# ---------------------------------------------------------------------------
def pick_demo_event(cfg, n_pe=8):
    """Find a seed whose event has both pile-up and well-separated pulses."""
    for seed in range(400):
        g = WaveformGenerator(cfg, seed=seed)
        ev = g.generate(channel_id=0, n_pe=n_pe)
        ts = np.sort([p.t_hit_ns for p in ev.truth])
        gaps = np.diff(ts)
        if len(ts) >= 7 and gaps.min() < 32 and gaps.max() > 150:
            return g, ev, seed
    raise RuntimeError("no suitable demo event found")


def fig2(cfg):
    g, ev, seed = pick_demo_event(cfg)
    tmpl, peak_i, _ = load_template(cfg)
    n = cfg.n_samples
    t = np.arange(n) * cfg.sample_interval_ns
    ts = np.array([p.t_hit_ns for p in ev.truth])
    amps = np.array([p.amplitude_pe for p in ev.truth])

    singles = np.vstack([place_pulse(tmpl, peak_i, tt, aa, n) for tt, aa in zip(ts, amps)])
    analog_mv = singles.sum(axis=0) * 1000.0

    lsb_v = cfg.lsb_v
    bl = cfg.baseline_adc
    sigma_counts = cfg.noise_sigma_mv * 1e-3 / lsb_v

    fig, axs = plt.subplots(
        4, 1, figsize=(8.8, 7.6), sharex=True, constrained_layout=True,
        gridspec_kw={"height_ratios": [0.8, 1.6, 1.2, 1.6]},
    )
    fig.suptitle(
        "Forward model: a few photoelectrons become one digitized waveform",
        fontsize=11, fontweight="bold", x=0.005, ha="left",
    )

    # (a) truth
    ax = axs[0]
    ax.vlines(ts, 0, amps, color=ORANGE, lw=2.0)
    ax.scatter(ts, amps, s=22, color=ORANGE, zorder=3)
    style_ax(ax)
    ax.set_ylim(0, max(amps) * 1.35)
    ax.set_ylabel("charge [pe]")
    ax.set_title(f"(a) ground truth: Poisson pulse train, here {len(ts)} hits (seed {seed})", loc="left")

    # (b) individual pulses
    ax = axs[1]
    for row in singles:
        ax.plot(t, row * 1000.0, color=AQUA, lw=1.3, alpha=0.85)
    style_ax(ax)
    ax.set_ylabel("height [mV]")
    ax.set_title("(b) each hit → one copy of the fixed pulse shape, scaled by its charge", loc="left")

    # (c) analog sum
    ax = axs[2]
    ax.plot(t, analog_mv, color=BLUE, lw=2.0)
    # annotate the closest pair if it is piled up
    order_g = np.argsort(ts)
    gaps = np.diff(ts[order_g])
    gi = int(np.argmin(gaps))
    t1, t2 = ts[order_g][gi], ts[order_g][gi + 1]
    if gaps[gi] < 40:
        m = (t >= min(t1, t2) - 5) & (t <= max(t1, t2) + 5)
        pk = analog_mv[m].max()
        ax.annotate(f"2 hits {gaps[gi]:.0f} ns apart\n→ one merged bump",
                    xy=(0.5 * (t1 + t2), pk * 0.9),
                    xytext=(0.5 * (t1 + t2) + 90, pk * 1.25), color=SEC,
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    style_ax(ax)
    ax.set_ylabel("height [mV]")
    ax.set_title("(c) they simply add: overlapping pulses merge into one bump (pile-up)", loc="left")

    # (d) digitized
    ax = axs[3]
    ax.axhspan(bl - 2 * sigma_counts, bl + 2 * sigma_counts, color=MUTED, alpha=0.14, lw=0)
    ax.axhline(bl, color=AXIS, lw=0.8)
    ax.step(t, ev.adc, where="post", color=INK, lw=1.0)
    for tt in ts:
        ax.axvline(tt, ymax=0.16, color=ORANGE, lw=1.4)
    style_ax(ax)
    ax.set_ylim(bl - max(90, 1.15 * (bl - ev.adc.min())), bl + 60)
    ax.set_ylabel("ADC counts")
    ax.set_xlabel("time [ns]")
    ax.set_title(
        "(d) what the detector records: negative polarity + white noise + 14-bit ADC "
        "(orange ticks = true hit times)", loc="left",
    )

    out = FIGDIR / "fig2_forward_model.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}  (demo seed {seed}, {len(ts)} hits)")


# ---------------------------------------------------------------------------
# figure 3 — pile-up in the frozen dataset
# ---------------------------------------------------------------------------
def build_cfg_from_meta(meta_json):
    meta = json.loads(str(meta_json))
    fields = {k: v for k, v in meta["config"].items() if k != "pulse_shape"}
    fields["pulse_shape"] = PulseShape(meta["config"]["pulse_shape"])
    return WaveGenConfig(**fields)


def pick_dataset_events(d, cfg):
    """One well-separated, one moderately piled-up, one severely merged event."""
    offs = d["t_offsets"]
    found = {}
    for ev_i in range(d["adc"].shape[0]):
        ts = d["t_hits"][offs[ev_i]: offs[ev_i + 1]]
        amps = d["amplitudes"][offs[ev_i]: offs[ev_i + 1]]
        if len(ts) < 3:
            continue
        gaps = np.diff(np.sort(ts))
        if "sep" not in found and gaps.min() > 70 and len(ts) >= 4:
            found["sep"] = ev_i
        if "mod" not in found and np.any((gaps >= 18) & (gaps <= 35)):
            found["mod"] = ev_i
        if "sev" not in found and np.any(gaps < 10) and amps.min() > 0.5:
            found["sev"] = ev_i
        if len(found) == 3:
            break
    assert len(found) == 3, found
    return found


def fig3(cfg):
    sys.path.insert(0, str(ROOT / "baselines"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from threshold_integrator import reconstruct  # noqa: E402
    from evaluate import match_pulses  # noqa: E402

    d = np.load(ROOT / "data/waverec_v1_snr_nominal.npz")
    chosen = pick_dataset_events(d, cfg)
    t = np.arange(cfg.n_samples) * cfg.sample_interval_ns
    tol = 20.0

    labels = {
        "sep": "well separated — easy",
        "mod": "moderate pile-up — bumps lean on each other",
        "sev": "severe pile-up — two true PEs look like one wider pulse",
    }
    fig, axs = plt.subplots(3, 1, figsize=(8.8, 6.9), sharex=True, constrained_layout=True)
    fig.suptitle(
        "The difficulty: pile-up in real dataset events (nominal sample)",
        fontsize=11, fontweight="bold", x=0.005, ha="left",
    )

    handles = {}
    for ax, key in zip(axs, ("sep", "mod", "sev")):
        ev_i = chosen[key]
        adc = d["adc"][ev_i]
        offs = d["t_offsets"]
        t_true = d["t_hits"][offs[ev_i]: offs[ev_i + 1]]
        t_rec, _ = reconstruct(adc.astype(float), cfg)
        pairs, order = match_pulses(t_true, np.ones_like(t_true), t_rec, np.ones_like(t_rec), tol)
        rec_matched = {order[ip] for _, ip in pairs}
        true_hit = {it for it, _ in pairs}

        # highlight the closest piled-up pair, if any
        srt = np.sort(t_true)
        if len(srt) >= 2:
            g = np.diff(srt)
            gi = int(np.argmin(g))
            if g[gi] < 45:
                ax.axvspan(srt[gi] - 25, srt[gi + 1] + 25, color=RED, alpha=0.06, lw=0)

        ln, = ax.step(t, adc, where="post", color=INK, lw=1.0, label="digitized waveform")
        tr = ax.get_xaxis_transform()  # x in data coords, y in axes fraction
        mk, = ax.plot(t_true, np.full(len(t_true), 0.955), linestyle="none", transform=tr,
                      marker="v", ms=6, color=ORANGE, markeredgecolor=SURF,
                      markeredgewidth=1.0, label="true hit time", zorder=5)
        rec_t = t_rec[t_rec <= t[-1]]
        m_mask = np.array([i in rec_matched for i in range(len(t_rec))])
        hm, = ax.plot(rec_t[m_mask], np.full(int(m_mask.sum()), 0.045), linestyle="none",
                      transform=tr, marker="o", ms=5.5, color=BLUE, markeredgecolor=SURF,
                      markeredgewidth=1.0, label="reconstructed (matched)", zorder=5)
        if int((~m_mask).sum()):
            ax.plot(rec_t[~m_mask], np.full(int((~m_mask).sum()), 0.045), linestyle="none",
                    transform=tr, marker="o", ms=5.5, markerfacecolor=SURF,
                    markeredgecolor=RED, markeredgewidth=1.6, zorder=6)
        missed = [tt for i, tt in enumerate(t_true) if i not in true_hit]
        if missed:
            ax.plot(missed, np.full(len(missed), 0.955), linestyle="none", transform=tr,
                    marker="v", ms=6, markerfacecolor=SURF, markeredgecolor=RED,
                    markeredgewidth=1.5, zorder=6)
        handles = {"wf": ln, "truth": mk, "match": hm}

        style_ax(ax)
        bl = cfg.baseline_adc
        ax.set_ylim(bl - 1.15 * max(60, bl - adc.min()), bl + 45)
        n_spur = int((~m_mask).sum())
        extra = f" · {n_spur} spurious" if n_spur else ""
        ax.set_title(f"{labels[key]}   ·   {len(t_true)} true PEs · "
                     f"{int(m_mask.sum())} matched · {len(missed)} lost to pile-up{extra}",
                     loc="right", fontsize=8, color=SEC, pad=3)
    axs[-1].set_xlabel("time [ns]")
    lost = Line2D([], [], linestyle="none", marker="v", ms=6, markerfacecolor=SURF,
                  markeredgecolor=RED, markeredgewidth=1.5, label="true PE lost to pile-up")
    fig.legend(handles=[handles["wf"], handles["truth"], handles["match"], lost],
               labels=["digitized waveform", "true hit time", "reconstructed (matched)",
                       "true PE lost to pile-up"],
               loc="outside lower center", ncols=4)

    out = FIGDIR / "fig3_pileup.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}  (dataset events {list(chosen.values())})")


def main():
    FIGDIR.mkdir(exist_ok=True)
    cfg = WaveGenConfig()
    fig1(cfg)
    fig2(cfg)
    fig3(cfg)


if __name__ == "__main__":
    main()
