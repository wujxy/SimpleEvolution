"""Baseline reconstructor: charge-sum energy + charge-centroid vertex.

The reference any agent must beat. Deliberately simple:

  energy  - total baseline-subtracted FADC charge over the window (summed
            over the stored channels, rescaled by the stored fraction),
            linearly calibrated through the origin on a train split;
  vertex  - charge-weighted centroid of hit-PMT positions (biased toward
            the near wall — that bias is part of what a better agent fixes);
  t0      - mean leading-edge time minus TOF from the charge centroid,
            offset-calibrated on the train split.

Usage:
  python3 baselines/charge_centroid.py --data blind_task_electron/test.npz \
      --train blind_task_electron/train.npz --out pred.npz
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench._vendor.wavegen_v1 import (
    WaveGenConfig,
)
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout


def load_positions(meta):
    if meta["layout"] == "juno":
        return PMTLayout.from_juno_csv().positions_m
    return PMTLayout.uniform(meta["n_pmt"], meta["radius_m"]).positions_m


def event_features(d, wave_cfg, pos):
    """Per-event (charge, centroid) from stored waveform channels."""
    adc = d["adc"]
    adc_ids = d["adc_pmt_ids"]
    n_ev = len(d["pmt_offsets"]) - 1

    base = int(round(wave_cfg.baseline_frac * ((1 << wave_cfg.adc_bits) - 1)))
    q_ch = np.clip(base - adc, 0, None).sum(axis=1).astype(np.float64)

    meta = json.loads(str(d["meta"]))
    max_wf = meta.get("max_wf_per_event", 0) or len(adc_ids)
    rows_per_ev = np.minimum(np.diff(d["pmt_offsets"]), max_wf).astype(np.int64)
    rows_per_ev = np.minimum(
        rows_per_ev,
        len(adc_ids) - np.concatenate(([0], np.cumsum(rows_per_ev)))[:-1],
    ).astype(np.int64)
    ev_of_row = np.repeat(np.arange(n_ev), rows_per_ev)

    q_evt = np.zeros(n_ev)
    c = np.zeros((n_ev, 3))
    np.add.at(q_evt, ev_of_row, q_ch)
    w = q_ch[:, None]
    np.add.at(c, ev_of_row, pos[adc_ids] * w)

    # stored channels are a random subset: rescale charge by stored fraction
    n_hit = np.diff(d["pmt_offsets"]).astype(np.float64)
    scale = np.where(rows_per_ev > 0, n_hit / np.maximum(rows_per_ev, 1.0), 0.0)
    q_evt *= scale

    tot = np.maximum(q_evt, 1e-9)[:, None]
    return q_evt, c / tot, ev_of_row


def leading_edge_times(d, wave_cfg, pos, centroid, ev_of_row):
    """Per-event mean (leading edge - TOF from centroid)."""
    adc = d["adc"]
    base = int(round(wave_cfg.baseline_frac * ((1 << wave_cfg.adc_bits) - 1)))
    sigma = wave_cfg.noise_sigma_mv * 1e-3 / wave_cfg.lsb_v
    below = adc < base - 5.0 * sigma
    has = below.any(axis=1)
    lead = np.where(has, below.argmax(axis=1), np.nan).astype(np.float64)

    rel = pos[d["adc_pmt_ids"]] - centroid[ev_of_row]
    tof = np.linalg.norm(rel, axis=1) / (0.299792458 / 1.49)
    t_est = lead - tof
    ok = np.isfinite(t_est)
    n_ev = len(d["pmt_offsets"]) - 1
    t_evt = np.zeros(n_ev)
    cnt = np.zeros(n_ev)
    np.add.at(t_evt, ev_of_row[ok], t_est[ok])
    np.add.at(cnt, ev_of_row[ok], 1.0)
    return t_evt / np.maximum(cnt, 1.0), cnt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="npz to predict on")
    ap.add_argument("--train", default=None,
                    help="truth-visible npz for calibration "
                         "(default: --data itself, first --n-train events)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-train", type=int, default=50)
    args = ap.parse_args()

    wave_cfg = WaveGenConfig()
    d = np.load(args.data, allow_pickle=False)
    d_tr = np.load(args.train, allow_pickle=False) if args.train else d

    meta = json.loads(str(d["meta"]))
    pos = load_positions(meta)

    q_evt, centroid, ev_row = event_features(d, wave_cfg, pos)
    q_tr, centroid_tr, ev_row_tr = event_features(d_tr, wave_cfg, pos)

    n_tr = min(args.n_train, len(q_tr) // 2)
    # calibrate against the scored reference (e+ includes 1.022 MeV
    # annihilation light, matching the evaluate.py convention)
    e_ref_tr = (d_tr["evt_e_scored"] if "evt_e_scored" in d_tr.files
                else d_tr["evt_e_true"])
    k = float(np.sum(e_ref_tr[:n_tr] * q_tr[:n_tr])
              / np.sum(q_tr[:n_tr] ** 2))

    t_evt, cnt = leading_edge_times(d, wave_cfg, pos, centroid, ev_row)
    t_tr, _ = leading_edge_times(d_tr, wave_cfg, pos, centroid_tr, ev_row_tr)
    # window-referenced truth (evaluate.py convention): sample 0 = t_trig - pre
    pre = meta.get("detector_config", {}).get("pre_trigger_ns", 300.0)
    t0_ref_tr = d_tr["evt_t0"][:n_tr] - (d_tr["evt_t_trigger"][:n_tr] - pre)
    t0_offset = float(np.median(t_tr[:n_tr] - t0_ref_tr))
    t0_rec = np.where(cnt > 0, t_evt - t0_offset, 0.0)

    np.savez(
        args.out,
        E_rec=k * q_evt,
        x_rec=centroid[:, 0], y_rec=centroid[:, 1], z_rec=centroid[:, 2],
        t0_rec=t0_rec,
        meta=np.array(json.dumps({"baseline": "charge_centroid", "k": k,
                                  "t0_offset": t0_offset})),
    )
    print(f"wrote {args.out} (k={k:.4e} MeV/count, t0_offset={t0_offset:.1f} ns)")


if __name__ == "__main__":
    main()
