"""Score agent predictions against JunoResBench truth.

Contract — a solver reads a dataset npz and writes a prediction npz with:

    E_rec  (N,)  float   reconstructed visible energy, MeV
    x_rec, y_rec, z_rec (N,) float   reconstructed vertex, m
    t0_rec (N,)  float   reconstructed event time, ns

Usage:
    python3 scripts/evaluate.py --data data/bench.npz --pred pred.npz

Metrics (see docs/stage_design.md stage 6):
  energy      resolution = std(E_rec/E_true), linearity = fit slope drift,
              bias = mean(E_rec/E_true) - 1
  vertex      resolution = 68% quantile of |r_rec - r_true| (cm)
  timing      resolution = std(t0_rec - t0) (ns)

Ranking: energy resolution first, then vertex, then timing.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def load_xy(data_path, pred_path):
    d = np.load(data_path, allow_pickle=False)
    p = np.load(pred_path, allow_pickle=False)
    e_true = d["evt_e_true"]
    r_true = np.column_stack((d["evt_x_m"], d["evt_y_m"], d["evt_z_m"]))
    t0 = d["evt_t0"]

    missing = {"E_rec", "x_rec", "y_rec", "z_rec", "t0_rec"} - set(p.files)
    if missing:
        raise SystemExit(f"prediction npz missing keys: {sorted(missing)}")
    e_rec = np.asarray(p["E_rec"], float)
    r_rec = np.column_stack(
        (p["x_rec"], p["y_rec"], p["z_rec"])
    ).astype(float)
    t0_rec = np.asarray(p["t0_rec"], float)
    if not (len(e_rec) == len(e_true) == len(r_rec) == len(t0_rec)):
        raise SystemExit("prediction length mismatch with truth")
    return e_true, e_rec, r_true, r_rec, t0, t0_rec


def robust_std(x):
    """Std after removing |z|>4 outliers (guards against few bad events)."""
    x = np.asarray(x, float)
    m, s = np.mean(x), np.std(x)
    keep = np.abs((x - m) / max(s, 1e-12)) < 4.0
    return float(np.std(x[keep])), float(keep.mean())


def score(e_true, e_rec, r_true, r_rec, t0, t0_rec):
    # ---- energy ----------------------------------------------------------
    ratio = e_rec / np.where(e_true > 0, e_true, np.nan)
    ok = np.isfinite(ratio)
    res, kept = robust_std(ratio[ok])
    bias = float(np.nanmean(ratio) - 1.0)
    # linearity: binned mean ratio across the E range, max deviation
    order = np.argsort(e_true[ok])
    e_s, r_s = e_true[ok][order], ratio[ok][order]
    nbin = 5
    edges = np.quantile(e_s, np.linspace(0, 1, nbin + 1))
    binned = [
        float(np.mean(r_s[(e_s >= edges[i]) & (e_s <= edges[i + 1])]))
        for i in range(nbin)
    ]
    nonlin = float(max(binned) - min(binned))

    # ---- vertex ----------------------------------------------------------
    dr = np.linalg.norm(r_rec - r_true, axis=1) * 100.0   # cm
    vres = float(np.quantile(dr, 0.68))
    vmean = float(np.mean(dr))

    # ---- timing ----------------------------------------------------------
    dt = t0_rec - t0
    tres, tkept = robust_std(dt)

    return {
        "n_events": int(len(e_true)),
        "energy": {
            "resolution": res,
            "bias": bias,
            "nonlinearity": nonlin,
            "binned_mean_ratio": [round(b, 4) for b in binned],
            "kept_fraction": kept,
        },
        "vertex": {"res_68_cm": vres, "mean_abs_cm": vmean},
        "timing": {"resolution_ns": tres, "kept_fraction": tkept},
    }


def composite(s):
    """Lower is better: energy resolution, ties broken by vertex then timing."""
    return (s["energy"]["resolution"], s["vertex"]["res_68_cm"],
            s["timing"]["resolution_ns"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="truth dataset npz")
    ap.add_argument("--pred", required=True, help="prediction npz")
    ap.add_argument("--out", default=None, help="write score json here")
    args = ap.parse_args()

    s = score(*load_xy(args.data, args.pred))
    s["composite"] = list(composite(s))
    text = json.dumps(s, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text)


if __name__ == "__main__":
    main()
