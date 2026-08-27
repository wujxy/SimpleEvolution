"""Score agent predictions against JunoResBench truth.

Contract — a solver reads a dataset npz and writes a prediction npz with:

    E_rec  (N,)  float   reconstructed visible energy, MeV
    x_rec, y_rec, z_rec (N,) float   reconstructed vertex, m
    t0_rec (N,)  float   event time measured from window start, ns
                       (sample 0 = t_trigger - pre_trigger_ns; the trigger
                       follows the event, so only the window-referenced t0
                       is observable — the truth reference is computed the
                       same way from evt_t0 and evt_t_trigger)

Usage:
    python3 scripts/evaluate.py --data data/bench.npz --pred pred.npz

Scored energy reference: evt_e_scored when present, else evt_e_true.
evt_e_scored = e_true + 1.022 MeV for positrons (the JUNO convention —
annihilation light belongs to the reconstructed energy scale, so the
reconstructed peak sits at kinetic + 1.022 MeV) and = e_true for
electrons/gammas.

Metrics (v1 convention — changed from the v0 std-based definitions):
  energy      resolution = (q84 - q16)/2 of E_rec/E_ref (quantile width:
              honest for the non-Gaussian gamma escape tail, no outlier
              rejection), bias = median(E_rec/E_ref) - 1, linearity =
              peak-to-peak of the 5-quantile-bin mean ratio
  vertex      resolution = 68% quantile of |r_rec - r_true| (cm)
  timing      resolution = (q84 - q16)/2 of t0_rec - t0_ref (ns), with
              t0_ref = evt_t0 - (evt_t_trigger - pre_trigger_ns)

When evt_particle_type is present, a "by_particle" breakdown reports the
same metrics per type (electron / gamma / positron).

Ranking: energy resolution first, then vertex, then timing.
"""

import argparse
import json
from pathlib import Path

import numpy as np

PTY_NAMES = {0: "electron", 1: "gamma", 2: "positron"}


def load_xy(data_path, pred_path):
    d = np.load(data_path, allow_pickle=False)
    p = np.load(pred_path, allow_pickle=False)
    e_ref = d["evt_e_scored"] if "evt_e_scored" in d.files else d["evt_e_true"]
    r_true = np.column_stack((d["evt_x_m"], d["evt_y_m"], d["evt_z_m"]))
    t0 = d["evt_t0"]
    if "evt_t_trigger" in d.files:
        # window-referenced event time: sample 0 sits at t_trigger - pre
        meta = json.loads(str(d["meta"])) if "meta" in d.files else {}
        pre = float(meta.get("detector_config", {}).get(
            "pre_trigger_ns", 300.0))
        t0 = t0 - (d["evt_t_trigger"] - pre)
    ptype = d["evt_particle_type"] if "evt_particle_type" in d.files else None

    missing = {"E_rec", "x_rec", "y_rec", "z_rec", "t0_rec"} - set(p.files)
    if missing:
        raise SystemExit(f"prediction npz missing keys: {sorted(missing)}")
    e_rec = np.asarray(p["E_rec"], float)
    r_rec = np.column_stack(
        (p["x_rec"], p["y_rec"], p["z_rec"])
    ).astype(float)
    t0_rec = np.asarray(p["t0_rec"], float)
    if not (len(e_rec) == len(e_ref) == len(r_rec) == len(t0_rec)):
        raise SystemExit("prediction length mismatch with truth")
    return e_ref, e_rec, r_true, r_rec, t0, t0_rec, ptype


def qwidth(x):
    """(q84 - q16)/2 — non-Gaussian-tail-honest half-width."""
    x = np.asarray(x, float)
    return float((np.quantile(x, 0.84) - np.quantile(x, 0.16)) / 2.0)


def metrics(e_ref, e_rec, r_true, r_rec, t0, t0_rec):
    # ---- energy ----------------------------------------------------------
    ratio = e_rec / np.where(e_ref > 0, e_ref, np.nan)
    ok = np.isfinite(ratio)
    res = qwidth(ratio[ok])
    bias = float(np.nanmedian(ratio) - 1.0)
    # linearity: binned mean ratio across the E range, max deviation
    order = np.argsort(e_ref[ok])
    e_s, r_s = e_ref[ok][order], ratio[ok][order]
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
    tres = qwidth(dt)

    return {
        "n_events": int(len(e_ref)),
        "energy": {
            "resolution": res,
            "bias": bias,
            "nonlinearity": nonlin,
            "binned_mean_ratio": [round(b, 4) for b in binned],
        },
        "vertex": {"res_68_cm": vres, "mean_abs_cm": vmean},
        "timing": {"resolution_ns": tres},
    }


def score(e_ref, e_rec, r_true, r_rec, t0, t0_rec, ptype=None):
    s = metrics(e_ref, e_rec, r_true, r_rec, t0, t0_rec)
    if ptype is not None:
        s["by_particle"] = {}
        for code in sorted(set(int(x) for x in ptype)):
            m = ptype == code
            if int(m.sum()) < 10:
                continue      # too few events for meaningful metrics
            name = PTY_NAMES.get(code, f"type{code}")
            sub = metrics(e_ref[m], e_rec[m], r_true[m], r_rec[m],
                          t0[m], t0_rec[m])
            sub.pop("binned_mean_ratio", None)
            s["by_particle"][name] = sub
    return s


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
