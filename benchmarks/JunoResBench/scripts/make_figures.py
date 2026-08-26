"""Plot intermediate-quantity distributions from a JunoResBench dataset.

    python3 scripts/make_figures.py --data data/test_small.npz

Produces figures/chain_distributions.png (E_true/E_vis/N_gamma/N_pe chain),
figures/timing.png (emission time, in-window hit time, SPE charge), and
figures/nonuniformity.png (pe/MeV vs vertex radius + model curve).
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=str, required=True)
    ap.add_argument("--out-dir", type=str, default=None)
    args = ap.parse_args()

    d = np.load(args.data, allow_pickle=False)
    meta = json.loads(str(d["meta"]))
    cfg = DetectorConfig(**{
        k: (tuple(map(tuple, v)) if k == "scint_taus_ns" else v)
        for k, v in meta["detector_config"].items()
    })
    fig_dir = Path(args.out_dir or Path(args.data).parent.parent / "figures")
    fig_dir.mkdir(parents=True, exist_ok=True)

    e_true = d["evt_e_true"]
    e_vis = d["evt_e_vis"]
    n_gamma = d["evt_n_gamma"]
    n_pe = d["evt_n_pe_total"]
    r = np.linalg.norm(
        np.column_stack((d["evt_x_m"], d["evt_y_m"], d["evt_z_m"])), axis=1
    )

    # ---- chain distributions -------------------------------------------
    fig, axes = plt.subplots(1, 5, figsize=(20, 3.4))
    for ax, (vals, title) in zip(axes, [
        (e_true, "E_true [MeV]"),
        (d["evt_e_dep"], "E_dep [MeV]"),
        (e_vis, "E_vis [MeV]"),
        (n_gamma / 1000.0, "N_gamma [x1000]"),
        (n_pe, "N_pe (in window)"),
    ]):
        ax.hist(vals, bins=50)
        ax.set_title(title)
        ax.set_yscale("log") if vals.min() > 0 and vals.max() / max(vals.min(), 1e-9) > 30 else None
    n_prod = d["evt_n_pe_produced"]
    axes[-1].axvline(np.mean(n_pe), color="r", ls="--", lw=1)
    fig.suptitle(
        f"forward chain, {len(e_true)} events | "
        f"mean pe/MeV={np.mean(n_pe / e_true):.0f} "
        f"(produced {np.mean(n_prod / e_true):.0f}, window cut "
        f"{100 * (1 - np.mean(n_pe) / np.mean(n_prod)):.1f}%)"
    )
    fig.tight_layout()
    fig.savefig(fig_dir / "chain_distributions.png", dpi=130)

    # ---- timing ----------------------------------------------------------
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.6))
    axes[0].hist(d["t_emit_ns"], bins=120, range=(0, 200))
    axes[0].set_title("scintillation emission time (ns)")
    axes[1].hist(d["t_rel_ns"][d["t_rel_ns"] < 1000], bins=120,
                 label="uniform vertices (TOF-convolved)")
    # reference: center-only events (TOF constant 96.2 ns -> pure emission
    # mixture + TTS), shifted to the same peak position
    from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
    from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
    sim_c = DetectorSim(cfg, PMTLayout.uniform(), seed=99)
    c = []
    for _ in range(150):
        e = sim_c.generate(0, 0, 0, float(np.median(e_true)),
                           with_waveforms=False)
        c.append(e.t_rel_ns - 96.2 + np.median(d["t_tof_ns"]))
    axes[1].hist(np.concatenate(c), bins=120, range=(0, 1000),
                 histtype="step", lw=1.5, color="r",
                 label="center-only (pure emission, no TOF spread)")
    axes[1].set_title("hit time rel. window start (ns)")
    axes[1].axvline(cfg.pre_trigger_ns, color="k", ls="--", lw=1, label="t0")
    axes[1].legend(fontsize=7)
    axes[2].hist(d["q_pe"], bins=80, range=(0, 4))
    axes[2].set_title("SPE charge (pe): core + tail + dark-free truth")
    for ax in axes:
        ax.set_yscale("log")
    fig.tight_layout()
    fig.savefig(fig_dir / "timing.png", dpi=130)

    # ---- staged chain: N_gamma -> N_pe -> per-PMT ------------------------
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.8))
    axes[0].scatter(e_vis, n_gamma, s=6, alpha=0.4)
    g0 = np.linspace(e_vis.min(), e_vis.max(), 10)
    axes[0].plot(g0, g0 * cfg.ly_photons_mev, "r-", lw=1)
    axes[0].set_xlabel("E_vis [MeV]"); axes[0].set_ylabel("N_gamma")
    axes[0].set_title(f"stage 1: N_gamma = E_vis x LY ({cfg.ly_photons_mev:.0f})")
    axes[1].scatter(n_gamma, n_pe, s=6, alpha=0.4)
    axes[1].set_xlabel("N_gamma"); axes[1].set_ylabel("N_pe (in window)")
    axes[1].set_title(f"stage 2: Binomial thinning, p_det={cfg.p_det_center:.3f} x mu(r)")
    # per-PMT: pe vs distance to vertex (charge-pattern pointing)
    ev_sel = np.argmax(e_vis)  # brightest event
    i0, i1 = d["pmt_offsets"][ev_sel], d["pmt_offsets"][ev_sel + 1]
    pids = d["pmt_ids"][i0:i1]
    axes[2].hist(d["n_pe_pmt"][i0:i1], bins=30)
    axes[2].set_yscale("log")
    axes[2].set_xlabel("pe per hit PMT"); axes[2].set_ylabel("PMTs")
    axes[2].set_title("stage 3: per-PMT pe (geometric weights)")
    fig.tight_layout()
    fig.savefig(fig_dir / "stages.png", dpi=130)

    # ---- nonuniformity ---------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    rr = np.linspace(0, cfg.nonuniform_radius_m, 100)
    model = cfg.mu_pe_per_mev_center * cfg.mu_pe_ratio(rr)
    per_pmt = np.add.reduceat(d["n_pe_pmt"], d["pmt_offsets"][:-1]) \
        if len(d["pmt_offsets"]) > 1 else d["n_pe_pmt"]
    # event-level: total pe / E vs radius
    sc = ax.scatter(r, n_pe / e_true, s=6, alpha=0.35, label="events")
    ax.plot(rr, model, "r-", lw=2,
            label=r"model $\mu_{pe}(r)$")
    ax.set_xlabel("vertex radius [m]")
    ax.set_ylabel("pe / MeV")
    ax.set_title("light-collection nonuniformity")
    ax.legend()
    fig.tight_layout()
    fig.savefig(fig_dir / "nonuniformity.png", dpi=130)

    print(f"wrote {fig_dir}/chain_distributions.png, stages.png, timing.png, "
          f"nonuniformity.png")


if __name__ == "__main__":
    main()
