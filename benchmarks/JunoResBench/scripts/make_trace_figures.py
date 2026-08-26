"""Trace-vs-fast comparison figures: propagation tail + wavelength red shift.

    python3 scripts/make_trace_figures.py

Produces figures/trace_tail.png (TOF distributions, fast vs trace) and
figures/trace_spectrum.png (emitted vs arrived wavelength).
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
from benchmarks.JunoResBench.juno_res_bench.optics_tables import (  # noqa: E402
    sample_emission_lambda,
)
from benchmarks.JunoResBench.juno_res_bench.stages import (  # noqa: E402
    s1_response,
    s2_photons,
    s3_trace,
)
from benchmarks.JunoResBench.juno_res_bench.truth import EventInput  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--n-events", type=int, default=100)
    args = ap.parse_args()
    out = Path(args.out_dir or Path(__file__).resolve().parents[1] / "figures")
    out.mkdir(parents=True, exist_ok=True)
    cfg = DetectorConfig()
    lay = PMTLayout.uniform()

    # ---- TOF distributions: fast vs trace --------------------------------
    tof_fast, tof_tr = [], []
    sim_f = DetectorSim(cfg, lay, seed=11)
    sim_t = DetectorSim(DetectorConfig(optics_mode="trace"), lay, seed=11)
    for _ in range(args.n_events):
        e = sim_f.generate(0, 0, 0, 1.0, with_waveforms=False)
        tof_fast.append(e.t_tof_ns)
        e = sim_t.generate(0, 0, 0, 1.0, with_waveforms=False)
        tof_tr.append(e.t_tof_ns)
    tof_fast, tof_tr = np.concatenate(tof_fast), np.concatenate(tof_tr)

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    b = np.linspace(90, 500, 120)
    ax.hist(tof_fast, bins=b, alpha=0.55, label=f"fast (folded): "
            f"mean {np.mean(tof_fast):.1f} ns")
    ax.hist(tof_tr, bins=b, alpha=0.55, label=f"trace: mean {np.mean(tof_tr):.1f} ns")
    ax.axvline(96.2, color="k", ls="--", lw=1, label="straight-line 96.2 ns")
    ax.set_yscale("log")
    ax.set_xlabel("time of flight vertex->PMT [ns] (center vertex)")
    ax.set_ylabel("PEs")
    ax.set_title("propagation tail: re-emission + Rayleigh path randomization")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "trace_tail.png", dpi=130)
    plt.close(fig)

    # ---- wavelength: emitted vs arrived ----------------------------------
    ev_in = EventInput(0, 0, 0, 2.0)
    s1 = s1_response.run_s1(ev_in, cfg)
    lam_em, lam_ar = [], []
    sim_t2 = DetectorSim(DetectorConfig(optics_mode="trace"), lay, seed=12)
    for _ in range(args.n_events):
        ph = s2_photons.run_s2_scint(s1, ev_in, cfg, np.random.default_rng(21))
        s3 = s3_trace.trace_photons(ph, ev_in, cfg, lay,
                                    np.random.default_rng(22),
                                    grid=sim_t2._get_dir_grid())
        lam_ar.append(s3.lam_nm)
        lam_em.append(ph.lam_nm if hasattr(ph, "lam_nm") else
                      sample_emission_lambda(np.random.default_rng(23),
                                             len(ph)))
    lam_ar = np.concatenate(lam_ar)
    lam_em = np.concatenate(lam_em)

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    b = np.linspace(320, 520, 100)
    ax.hist(lam_em, bins=b, alpha=0.5, density=True,
            label=f"emitted: mean {np.mean(lam_em):.1f} nm")
    ax.hist(lam_ar, bins=b, alpha=0.5, density=True,
            label=f"arrived: mean {np.mean(lam_ar):.1f} nm (red shift)")
    ax.axvline(420, color="k", ls=":", lw=1, label="fluor band edge")
    ax.set_xlabel("wavelength [nm]")
    ax.set_ylabel("density")
    ax.set_title("UV photons absorbed + re-emitted at 430 nm (T2)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "trace_spectrum.png", dpi=130)
    plt.close(fig)
    print(f"wrote {out}/trace_tail.png, trace_spectrum.png")


if __name__ == "__main__":
    main()
