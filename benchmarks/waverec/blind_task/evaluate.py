#!/usr/bin/env python3
"""Score waveform-reconstruction predictions against dataset truth.

Matching: predicted pulses are greedily matched to true pulses within a time
tolerance (nearest first); one-to-one, each true pulse at most once.

Metrics per dataset:
  n_true, n_pred          pulse counts
  efficiency              matched / n_true
  purity                  matched / n_pred
  time_rmse_ns            RMS of (t_pred - t_true) over matches
  charge_rel_bias         mean(q_pred - q_true) / mean(q_true) over matches
  charge_rel_rmse         RMS of (q_pred - q_true) / mean(q_true)
"""

import argparse
import json
import pathlib
import sys

import numpy as np


def match_pulses(t_true, a_true, t_pred, a_pred, tol_ns: float):
    """Greedy nearest matching; returns indices (i_true, i_pred) pairs."""
    if len(t_true) == 0 or len(t_pred) == 0:
        return [], []
    # candidate pairs within tolerance
    order = np.argsort(t_pred)
    t_pred_sorted = t_pred[order]
    lo = np.searchsorted(t_pred_sorted, t_true - tol_ns, side="left")
    hi = np.searchsorted(t_pred_sorted, t_true + tol_ns, side="right")
    cands = []
    for it in range(len(t_true)):
        for ip in range(lo[it], hi[it]):
            cands.append((abs(t_pred_sorted[ip] - t_true[it]), it, ip))
    cands.sort()
    used_t, used_p, pairs = set(), set(), []
    for dt, it, ip in cands:
        if it in used_t or ip in used_p:
            continue
        used_t.add(it)
        used_p.add(ip)
        pairs.append((it, ip))
    return pairs, order


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True)
    p.add_argument("--pred", required=True)
    p.add_argument("--tol-ns", type=float, default=20.0)
    p.add_argument("--json-out", default=None)
    args = p.parse_args()

    d = np.load(args.data)
    pr = np.load(args.pred)

    n_events = d["adc"].shape[0]
    t_off = d["t_offsets"]
    a_off = pr["t_offsets"]

    n_true_total = n_pred_total = n_match_total = 0
    dt_all, dq_all = [], []
    for ev in range(n_events):
        t_true = d["t_hits"][t_off[ev] : t_off[ev + 1]]
        a_true = d["amplitudes"][t_off[ev] : t_off[ev + 1]]
        t_pred = pr["t_pred"][a_off[ev] : a_off[ev + 1]]
        a_pred = pr["a_pred"][a_off[ev] : a_off[ev + 1]]
        pairs, order = match_pulses(t_true, a_true, t_pred, a_pred, args.tol_ns)
        n_true_total += len(t_true)
        n_pred_total += len(t_pred)
        n_match_total += len(pairs)
        for it, ip in pairs:
            dt_all.append(t_pred[order[ip]] - t_true[it])
            dq_all.append(a_pred[order[ip]] - a_true[it])

    eff = n_match_total / max(n_true_total, 1)
    pur = n_match_total / max(n_pred_total, 1)
    dt = np.asarray(dt_all)
    dq = np.asarray(dq_all)
    time_rmse = float(np.sqrt(np.mean(dt**2))) if dt.size else float("nan")
    mean_q = float(np.mean([d["amplitudes"].mean()]))
    q_bias = float(np.mean(dq) / mean_q) if dq.size else float("nan")
    q_rmse = float(np.sqrt(np.mean(dq**2)) / mean_q) if dq.size else float("nan")

    report = {
        "data": args.data,
        "pred": args.pred,
        "tol_ns": args.tol_ns,
        "n_true": n_true_total,
        "n_pred": n_pred_total,
        "n_matched": n_match_total,
        "efficiency": eff,
        "purity": pur,
        "time_rmse_ns": time_rmse,
        "charge_rel_bias": q_bias,
        "charge_rel_rmse": q_rmse,
    }
    print(json.dumps(report, indent=2))
    if args.json_out:
        pathlib.Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(args.json_out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
