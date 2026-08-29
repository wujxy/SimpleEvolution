"""Reconstruction summary figures for the jrb white-box electron runs.

Materializes each arm's final solver (last snapshot), runs it on the
held-out test set, and plots — against the charge-centroid baseline:

  (a) E_rec/E_ref distribution (quantile width = energy resolution)
  (b) energy resolution vs true radius
  (c) |dr| distribution, log x (q68 = vertex resolution)
  (d) vertex resolution vs true radius

    python3 scripts/fig_jrb_wb_final.py \
        --arms scientist=runs/singlenode/jrb-wb-elec-nolimit-scientist \
               coding=runs/singlenode/jrb-wb-elec-nolimit-coding \
        --out runs/singlenode/jrb-wb-elec-nolimit/figures
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "examples/junoresbench_wb_opt/repo"
TRUTH = REPO_ROOT / "benchmarks/JunoResBench/blind_truth_electron/test_full.npz"
VENV = "/datafs/users/wujxy/py_venv/my_env/bin"

# categorical slots 1/2 (validated) for the two arms; the baseline is a
# reference entity — dark gray dashed, never a third series hue.
STYLE = {
    "scientist": dict(color="#2a78d6", lw=2.0, ls="-"),
    "coding": dict(color="#eb6834", lw=2.0, ls="-"),
    "baseline": dict(color="#55564f", lw=1.6, ls="--"),
}
LABEL = {"scientist": "scientist", "coding": "coding",
         "baseline": "baseline (charge centroid)"}

plt.rcParams.update({
    "font.size": 10, "axes.titlesize": 10.5, "axes.labelsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": "#e8e8e4", "grid.linewidth": 0.7,
    "axes.axisbelow": True, "figure.facecolor": "white",
    "legend.frameon": False,
})


def last_snapshot(run: Path) -> Path | None:
    snaps = sorted((run / "snapshots").glob("seq-*"),
                   key=lambda p: int(p.name.split("-")[1]))
    return snaps[-1] if snaps else None


def predict(run: Path | None, td: Path, tag: str) -> dict:
    """Materialize base+overlay, run solver on test, return per-event cols."""
    workdir = td / tag
    shutil.copytree(BASE, workdir, ignore=shutil.ignore_patterns(".git"))
    if run is not None:  # None -> pristine baseline
        snap = last_snapshot(run)
        shutil.rmtree(workdir / "src")
        shutil.copytree(snap, workdir / "src")
    pkg = workdir / "benchmarks/whitebox_task_electron"
    pred = td / f"pred_{tag}.npz"
    proc = subprocess.run(
        ["python3", "src/solve.py", "--data", str(pkg / "test.npz"),
         "--train", str(pkg / "train.npz"), "--out", str(pred)],
        cwd=workdir, env={"PATH": f"{VENV}:{__import__('os').environ['PATH']}",
                          "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise SystemExit(f"solver failed for {tag}:\n{proc.stderr[-800:]}")

    t = np.load(TRUTH, allow_pickle=False)
    p = np.load(pred, allow_pickle=False)
    meta = json.loads(str(t["meta"]))
    pre = float(meta.get("detector_config", {}).get("pre_trigger_ns", 300.0))
    r_true = np.column_stack((t["evt_x_m"], t["evt_y_m"], t["evt_z_m"]))
    t0_ref = t["evt_t0"] - (t["evt_t_trigger"] - pre)
    e_ref = t["evt_e_scored"] if "evt_e_scored" in t.files else t["evt_e_true"]
    r_rec = np.column_stack((p["x_rec"], p["y_rec"], p["z_rec"]))
    return {
        "ratio": p["E_rec"] / e_ref,
        "dr_cm": np.linalg.norm(r_rec - r_true, axis=1) * 100.0,
        "r_true": np.linalg.norm(r_true, axis=1),
        "E_ref": e_ref,
        "dt": p["t0_rec"] - t0_ref,
    }


def qwidth(x):
    return (np.quantile(x, 0.84) - np.quantile(x, 0.16)) / 2.0


def binned(x, y, fn, nbin=5):
    edges = np.quantile(x, np.linspace(0, 1, nbin + 1))
    ctr, val = [], []
    for i in range(nbin):
        m = (x >= edges[i]) & (x <= edges[i + 1])
        if m.sum() < 8:
            continue
        ctr.append(float(x[m].mean()))
        val.append(float(fn(y[m])))
    return np.array(ctr), np.array(val)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", required=True,
                    help="tag=run_dir pairs (baseline added automatically)")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    data = {}
    with tempfile.TemporaryDirectory(prefix="jrbfig-") as td:
        data["baseline"] = predict(None, Path(td), "baseline")
        for spec in args.arms:
            tag, run = spec.split("=", 1)
            data[tag] = predict(Path(run), Path(td), tag)

    np.savez(args.out / "per_event.npz",
             **{f"{k}_{c}": v for k, d in data.items()
                for c, v in d.items()})

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.6))
    (ax_a, ax_b), (ax_c, ax_d) = axes

    # (a) energy: E_rec/E_ref distribution
    bins = np.linspace(0.55, 1.45, 91)
    for tag, d in data.items():
        st = STYLE[tag]
        ax_a.hist(np.clip(d["ratio"], bins[0], bins[-1]), bins=bins,
                  histtype="step", density=True,
                  color=st["color"], lw=st["lw"], ls=st["ls"],
                  label=f"{LABEL[tag]}  ({qwidth(d['ratio'])*100:.2f}%)")
    ax_a.set_xlabel(r"$E_{\rm rec}/E_{\rm ref}$")
    ax_a.set_ylabel("density")
    ax_a.set_title("(a) energy: quantile width "
                   r"$(q_{84}-q_{16})/2$" + " = resolution")
    ax_a.legend(loc="upper right", fontsize=8.5)
    ax_a.set_xlim(bins[0], bins[-1])
    ax_a.set_ylim(top=ax_a.get_ylim()[1] * 1.22)  # legend headroom

    # (b) energy resolution vs radius
    for tag, d in data.items():
        st = STYLE[tag]
        ctr, val = binned(d["r_true"], d["ratio"], qwidth)
        ax_b.plot(ctr, val * 100, marker="o", ms=4.5,
                  color=st["color"], lw=st["lw"], ls=st["ls"],
                  label=LABEL[tag])
    ax_b.set_xlabel(r"true vertex radius $r$ [m]")
    ax_b.set_ylabel("energy resolution [%]")
    ax_b.set_title("(b) energy resolution vs radius")
    ax_b.set_yscale("log")
    ax_b.legend(fontsize=8.5)

    # (c) vertex: |dr| distribution, log x
    dbins = np.logspace(0, np.log10(2200), 80)
    for tag, d in data.items():
        st = STYLE[tag]
        ax_c.hist(d["dr_cm"], bins=dbins, histtype="step", density=True,
                  color=st["color"], lw=st["lw"], ls=st["ls"],
                  label=f"{LABEL[tag]}  (q68 = "
                        f"{np.quantile(d['dr_cm'], 0.68):.0f} cm)")
    ax_c.set_xscale("log")
    ax_c.set_xlabel(r"$|\vec r_{\rm rec}-\vec r_{\rm true}|$ [cm]")
    ax_c.set_ylabel("density")
    ax_c.set_title("(c) vertex error: q68 = resolution")
    ax_c.set_ylim(top=ax_c.get_ylim()[1] * 1.45)  # legend headroom
    ax_c.legend(loc="upper left", fontsize=8.5)

    # (d) vertex resolution vs radius
    for tag, d in data.items():
        st = STYLE[tag]
        ctr, val = binned(d["r_true"], d["dr_cm"],
                          lambda x: np.quantile(x, 0.68))
        ax_d.plot(ctr, val, marker="o", ms=4.5,
                  color=st["color"], lw=st["lw"], ls=st["ls"],
                  label=LABEL[tag])
    ax_d.set_xlabel(r"true vertex radius $r$ [m]")
    ax_d.set_ylabel("vertex resolution (q68 |dr|) [cm]")
    ax_d.set_title("(d) vertex resolution vs radius")
    ax_d.set_yscale("log")
    ax_d.legend(fontsize=8.5)

    fig.suptitle("JunoResBench white-box electron — held-out test "
                 f"(n={len(data['baseline']['ratio'])}, 192/4808 channels "
                 "read out)", fontsize=12, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = args.out / "reconstruction_summary.png"
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")

    # numbers the panels carry
    for tag, d in data.items():
        print(f"{tag:10s} E_res {qwidth(d['ratio'])*100:6.2f}%  "
              f"bias {np.median(d['ratio'])-1:+.3f}  "
              f"V_q68 {np.quantile(d['dr_cm'],0.68):7.1f} cm  "
              f"T_res {qwidth(d['dt']):5.2f} ns")


if __name__ == "__main__":
    main()
