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


# ---------------------------------------------------------------------------
# figure 4 — TTS and dark noise on the same physics event
# ---------------------------------------------------------------------------
TTS_SIGMA = 5.0        # ns, representative (JUNO 20" LPMT: FWHM 10-15 ns)
DARK_RATE_HZ = 2e6     # EXAGGERATED so it is visible (real: 10-50 kHz)
NOISE_SEED = 777       # fresh generator per row -> identical electronics noise
ZOOM_W = 220.0         # ns


def sample_physics(rng, cfg, mean_pe, tts_sigma):
    """Mirror generate()/_sample_times/_sample_amplitudes, keeping the pre-TTS
    photon times (which the generator itself never exposes)."""
    p = cfg.spe
    n_pe = int(rng.poisson(mean_pe))
    gain = 1.0 + cfg.gain_spread * rng.normal()
    lo = cfg.pulse_length_ns * cfg.sample_interval_ns
    hi = (cfg.n_samples - cfg.pulse_length_ns) * cfg.sample_interval_ns
    t_phot = rng.uniform(lo, hi, n_pe)
    t_ano = t_phot + rng.normal(0.0, tts_sigma, n_pe)
    u = rng.random(n_pe)
    tail = rng.exponential(p.tail_decay, size=n_pe) + p.tail_cutoff
    core = rng.normal(p.gain, p.sigma_gain, size=n_pe)
    amp = np.where(u < p.p_tail, tail, core)
    np.clip(amp, 1e-4, None, out=amp)
    return t_phot, t_ano, amp * gain, gain


def sample_dark(rng, cfg, gain, rate_hz):
    """Dark pulses, emulated in this script: Poisson count, times uniform over
    the FULL window (dark counts are uncorrelated with the readout and may sit
    in the edge guard bands), SPE charges. `dark_rate_hz` is only a config
    hook — the generator does not sample dark pulses yet."""
    p = cfg.spe
    window_s = cfg.n_samples * cfg.sample_interval_ns * 1e-9
    k = int(rng.poisson(rate_hz * window_s))
    t = rng.uniform(0.0, cfg.n_samples * cfg.sample_interval_ns, k)
    u = rng.random(k)
    tail = rng.exponential(p.tail_decay, size=k) + p.tail_cutoff
    core = rng.normal(p.gain, p.sigma_gain, size=k)
    amp = np.clip(np.where(u < p.p_tail, tail, core), 1e-4, None) * gain
    return t, amp


def synth(cfg, times, amps):
    """Waveform with a fixed noise realization (fresh generator each call)."""
    g = WaveformGenerator(cfg, seed=NOISE_SEED)
    return g._synthesize(np.asarray(times, float), np.asarray(amps, float))


def pick_seed(cfg):
    """Find a physics seed with pile-up, a visible TTS shift and >=2 dark PEs
    (one inside the zoom span) so all three effects are visible."""
    for seed in range(20260800, 20260800 + 800):
        rng = np.random.default_rng(seed)
        t_phot, t_ano, amp, gain = sample_physics(rng, cfg, 10.0, TTS_SIGMA)
        t_dark, _ = sample_dark(rng, cfg, gain, DARK_RATE_HZ)
        srt = np.sort(t_phot)
        gaps = np.diff(srt)
        if not 9 <= len(t_phot) <= 12:
            continue
        if not 5 < gaps.min() < 40:
            continue
        c = 0.5 * (srt[gaps.argmin()] + srt[gaps.argmin() + 1])
        z0, z1 = c - ZOOM_W / 2, c + ZOOM_W / 2
        if len(t_dark) < 2 or not np.any((t_dark > z0 + 30) & (t_dark < z1 - 30)):
            continue
        dt = t_ano - t_phot
        if not np.any(np.abs(dt[(t_phot > z0) & (t_phot < z1)]) > 6.0):
            continue
        return seed, z0, z1
    raise RuntimeError("no seed found")


def fig4(cfg):
    seed, z0, z1 = pick_seed(cfg)
    rng = np.random.default_rng(seed)
    t_phot, t_ano, amp, gain = sample_physics(rng, cfg, 10.0, TTS_SIGMA)
    t_dark, a_dark = sample_dark(rng, cfg, gain, DARK_RATE_HZ)

    adc_a = synth(cfg, t_phot, amp)
    adc_b = synth(cfg, t_ano, amp)
    adc_c = synth(cfg, np.concatenate([t_phot, t_dark]), np.concatenate([amp, a_dark]))

    t = np.arange(cfg.n_samples) * cfg.sample_interval_ns
    bl = cfg.baseline_adc
    adc_min = min(adc_a.min(), adc_b.min(), adc_c.min())

    rows = [
        (adc_a, "(a) default: TTS = 0, dark rate = 0 — pulses land exactly at the hit times"),
        (adc_b, f"(b) + transit-time spread (TTS) sigma = {TTS_SIGMA:.0f} ns: hit times wander "
                "by N(0, sigma); charge and pulse shape untouched"),
        (adc_c, f"(c) + dark noise at {DARK_RATE_HZ/1e6:.0f} MHz (exaggerated; real 20\" PMTs "
                "are 10-50 kHz): extra 1-pe pulses with no physics hit behind them"),
    ]

    fig, axs = plt.subplots(
        3, 2, figsize=(11.4, 7.6), constrained_layout=True,
        gridspec_kw={"width_ratios": [2.1, 1.0]},
    )
    fig.suptitle(
        f"TTS and dark noise on the same physics event — {len(t_phot)} PEs, seed {seed}",
        fontsize=11, fontweight="bold", x=0.005, ha="left",
    )

    dt_ano = t_ano - t_phot
    dt_zoom_idx = (t_phot > z0) & (t_phot < z1)

    for r, (adc, title) in enumerate(rows):
        for c_idx in (0, 1):
            ax = axs[r, c_idx]
            ax.axhline(bl, color=AXIS, lw=0.8)
            ax.step(t, adc, where="post", color=INK, lw=1.0)
            tr = ax.get_xaxis_transform()

            if c_idx == 0 or r != 1:
                # rail: anode truth (post-TTS times the benchmark would record)
                times_rail = t_ano if (r == 1 and c_idx == 0) else t_phot
                ax.plot(times_rail, np.full(len(times_rail), 0.955), linestyle="none",
                        transform=tr, marker="v", ms=6, color=ORANGE,
                        markeredgecolor=SURF, markeredgewidth=1.0, zorder=5)
            else:
                # zoom rail (row b): photon arrival (pre-TTS) vs anode hit, paired
                for tp, ta in zip(t_phot, t_ano):
                    ax.plot([tp, ta], [0.885, 0.955], color=MUTED, lw=0.7,
                            transform=tr, zorder=4)
                ax.plot(t_phot, np.full(len(t_phot), 0.885), linestyle="none",
                        transform=tr, marker="^", ms=6, color=AQUA,
                        markeredgecolor=SURF, markeredgewidth=1.0, zorder=5)
                ax.plot(t_ano, np.full(len(t_ano), 0.955), linestyle="none",
                        transform=tr, marker="v", ms=6, color=ORANGE,
                        markeredgecolor=SURF, markeredgewidth=1.0, zorder=5)

            if r == 2:
                ax.plot(t_dark, np.full(len(t_dark), 0.045), linestyle="none",
                        transform=tr, marker="x", ms=7, color=RED,
                        markeredgewidth=1.8, zorder=6)

            ax.set_xlim(z0, z1) if c_idx else ax.set_xlim(0, cfg.n_samples)
            ax.set_ylim(bl - 1.15 * max(60, bl - adc_min), bl + 45)
            if r == 0:
                ax.set_title("full 1000 ns window" if c_idx == 0
                             else f"zoom {z0:.0f}-{z1:.0f} ns (same span every row)",
                             loc="left", color=SEC, fontsize=8.5)
            else:
                ax.set_title(title if c_idx == 0 else "", loc="left", color=SEC, fontsize=8.5)
            if c_idx == 0:
                ax.set_ylabel("ADC counts")
            style_ax(ax)
            if r == 2:
                ax.set_xlabel("time [ns]")

    # annotation (b): largest TTS displacement inside the zoom
    axb = axs[1, 1]
    tr = axb.get_xaxis_transform()
    cand = np.where(dt_zoom_idx)[0]
    i = cand[np.argmax(np.abs(dt_ano[cand]))]
    axb.annotate(
        f"t_hit wanders by {dt_ano[i]:+.0f} ns",
        xy=(0.5 * (t_phot[i] + t_ano[i]), 0.80), xytext=(z0 + 0.58 * ZOOM_W, 0.62),
        transform=tr, color=SEC,
        arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8),
    )
    # annotation (c): one dark pulse hump in the zoom
    axc = axs[2, 1]
    d_in = t_dark[(t_dark > z0 + 30) & (t_dark < z1 - 30)]
    if len(d_in):
        td = d_in[np.argmin(np.abs(d_in - 0.5 * (z0 + z1)))]
        tr = axc.get_xaxis_transform()
        axc.annotate(
            "dark PE: a pulse with no physics hit",
            xy=(td, 0.30), xytext=(z0 + 0.30 * ZOOM_W, 0.55),
            transform=tr, color=SEC,
            arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8),
        )

    handles = [
        Line2D([], [], color=INK, lw=1.0, label="digitized waveform"),
        Line2D([], [], linestyle="none", marker="^", ms=6, color=AQUA,
               markeredgecolor=SURF, label="photon arrival (pre-TTS, never recorded)"),
        Line2D([], [], linestyle="none", marker="v", ms=6, color=ORANGE,
               markeredgecolor=SURF, label="anode hit time (the benchmark truth)"),
        Line2D([], [], linestyle="none", marker="x", ms=7, color=RED,
               markeredgewidth=1.8, label="dark-noise PE"),
    ]
    fig.legend(handles=handles, loc="outside lower center", ncols=4)

    out = FIGDIR / "fig4_tts_dark.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"wrote {out}  (seed {seed}, {len(t_phot)} PEs, "
          f"{len(t_dark)} dark PEs, zoom {z0:.0f}-{z1:.0f} ns)")


def main():
    FIGDIR.mkdir(exist_ok=True)
    cfg = WaveGenConfig()
    fig1(cfg)
    fig2(cfg)
    fig3(cfg)
    fig4(cfg)


if __name__ == "__main__":
    main()
