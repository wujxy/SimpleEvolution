"""Hit-time comparison figure: scintillation vs Cherenkov photons.

    python3 scripts/make_hit_time_figure.py [--n-events 50] [--e-mev 5.0]

Accumulates trace-mode arrivals split by photon_type and draws two panels:
t_arrive (true hit time = emission + propagation) and t_tof (propagation
only). Cherenkov is prompt, so its t_arrive directly images the transport
kernel; scintillation is smeared by the multi-exponential emission spectrum.
Output: figures/hit_time_by_type.png.
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
from benchmarks.JunoResBench.juno_res_bench.geometry import DirectionGrid, PMTLayout  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.rng import make_rngs  # noqa: E402
from benchmarks.JunoResBench.juno_res_bench.stages import (  # noqa: E402
    s1_response,
    s2_photons,
    s3_trace,
)
from benchmarks.JunoResBench.juno_res_bench.truth import EventInput, PhotonSoA  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-events", type=int, default=50)
    ap.add_argument("--e-mev", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=31)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    out = Path(args.out_dir or Path(__file__).resolve().parents[1] / "figures")
    out.mkdir(parents=True, exist_ok=True)

    cfg = DetectorConfig(optics_mode="trace")
    lay = PMTLayout.uniform()
    grid = DirectionGrid.for_layout(lay, n_theta=360)

    arr = {0: [], 1: []}   # photon_type -> t_arrive lists
    tof = {0: [], 1: []}
    for i in range(args.n_events):
        ev = EventInput(0.0, 0.0, 0.0, args.e_mev)
        rngs = make_rngs(args.seed + i)
        s1 = s1_response.run_s1(ev, cfg)
        ph = PhotonSoA.concatenate([
            s2_photons.run_s2_scint(s1, ev, cfg, rngs["s2_scint"]),
            s2_photons.run_s2_cherenkov(s1, ev, cfg, rngs["s2_cherenkov"]),
        ])
        s3 = s3_trace.trace_photons(ph, ev, cfg, lay, rngs["s3_optics"], grid=grid)
        pt = ph.photon_type[s3.photon_idx]
        ta = np.asarray(s3.t_arrive_ns, dtype=float)
        tt = np.asarray(s3.t_tof_ns, dtype=float)
        for k in (0, 1):
            m = pt == k
            arr[k].append(ta[m])
            tof[k].append(tt[m])
    arr = {k: np.concatenate(v) for k, v in arr.items()}
    tof = {k: np.concatenate(v) for k, v in tof.items()}

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
    labels = {0: "scintillation", 1: "Cherenkov"}
    colors = {0: "#1f77b4", 1: "#d62728"}

    panels = [
        ("t_arrive_ns", arr, "photon hit time at PMT [ns]",
         f"emission spectrum x transport (peak ~ t0+{np.median(arr[0]):.0f} ns)"),
        ("t_tof_ns", tof, "propagation time vertex->PMT [ns]",
         "transport kernel only (Cherenkov is prompt)"),
    ]
    for ax, (key, data, xlabel, sub) in zip(axes, panels):
        hi = data[0].max()
        b = np.linspace(0, min(hi, 700), 140)
        for k in (0, 1):
            x = data[k]
            ax.hist(x, bins=b, density=True, alpha=0.55, color=colors[k],
                    label=f"{labels[k]}: n={len(x)}, "
                          f"p99={np.percentile(x, 99):.0f} ns")
        ax.set_yscale("log")
        ax.set_ylim(1e-6, None)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("density [1/ns]")
        ax.set_title(sub, fontsize=10)
        ax.legend(fontsize=9)

    fig.suptitle(
        f"scintillation vs Cherenkov hit times ({args.n_events} events, "
        f"{args.e_mev:.0f} MeV @ center, trace optics)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out / "hit_time_by_type.png", dpi=130)
    plt.close(fig)
    print(f"wrote {out}/hit_time_by_type.png")


if __name__ == "__main__":
    main()
