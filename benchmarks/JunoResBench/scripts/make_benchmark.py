"""Build the frozen JunoResBench benchmark package.

Produces (following the waverec blind-task pattern):
  data/jrb_bench_v1.npz        full dataset with truth (private)
  blind_task/train.npz         truth visible (agent calibrates on it)
  blind_task/val.npz           truth visible
  blind_task/test.npz          adc + geometry only, meta/truth stripped
  blind_task/TASK.md           the task sheet given to agents
  blind_task/evaluate.py       standalone scorer (numpy only)
  blind_truth/test_full.npz    private test truth + reference score

Usage:
  python3 scripts/make_benchmark.py --events 300 --seed 20260910
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BENCH = Path(__file__).resolve().parents[1]


def strip_to_observation(d, keys):
    """Agent observation: waveforms, channel ids, geometry — no truth."""
    out = {}
    for k in ("adc", "adc_pmt_ids", "pmt_offsets", "pe_offsets"):
        if k in d.files and k in keys:
            out[k] = d[k]
    out["meta"] = d["meta"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=300)
    ap.add_argument("--emin", type=float, default=1.0)
    ap.add_argument("--emax", type=float, default=8.0)
    ap.add_argument("--rmax", type=float, default=16.0)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--max-wf-per-event", type=int, default=192)
    ap.add_argument("--layout", choices=["uniform", "juno"], default="uniform")
    ap.add_argument("--optics-mode", choices=["fast", "trace"], default="trace",
                    help="trace = physical propagation tails + red shift")
    args = ap.parse_args()

    # 1. generate the full dataset
    full_path = BENCH / "data" / "jrb_bench_v1.npz"
    subprocess = __import__("subprocess")
    cmd = [
        sys.executable, str(BENCH / "scripts" / "generate_dataset.py"),
        "--events", str(args.events), "--emin", str(args.emin),
        "--emax", str(args.emax), "--rmax", str(args.rmax),
        "--seed", str(args.seed), "--layout", args.layout,
        "--max-wf-per-event", str(args.max_wf_per_event),
        "--optics-mode", args.optics_mode,
        "--out", str(full_path),
    ]
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True)

    d = np.load(full_path, allow_pickle=False)
    meta = json.loads(str(d["meta"]))
    n = len(d["evt_e_true"])
    n_tr, n_val = int(0.4 * n), int(0.2 * n)
    idx = {"train": np.arange(0, n_tr),
           "val": np.arange(n_tr, n_tr + n_val),
           "test": np.arange(n_tr + n_val, n)}

    blind = BENCH / "blind_task"
    private = BENCH / "blind_truth"
    blind.mkdir(exist_ok=True)
    private.mkdir(exist_ok=True)

    def subset(indices):
        keep = np.zeros(n, bool)
        keep[indices] = True
        out = {}
        # event-level arrays
        for k in d.files:
            if k == "meta":
                out[k] = d[k]
            elif k in ("pmt_offsets", "pe_offsets"):
                out[k] = np.append(d[k][:-1][keep], d[k][-1])
            elif k.startswith("evt_"):
                out[k] = d[k][keep]
        # ragged per-PMT arrays (slice by pmt_offsets)
        sel_ev = np.where(keep)[0]
        pmt_slices = np.concatenate(
            [np.arange(d["pmt_offsets"][i], d["pmt_offsets"][i + 1])
             for i in sel_ev]
        ) if len(sel_ev) else np.zeros(0, int)
        for k in ("pmt_ids", "n_pe_pmt"):
            out[k] = d[k][pmt_slices]
        # ragged per-PE arrays (slice by pe_offsets)
        pe_slices = np.concatenate(
            [np.arange(d["pe_offsets"][i], d["pe_offsets"][i + 1])
             for i in sel_ev]
        ) if len(sel_ev) else np.zeros(0, int)
        for k in ("t_emit_ns", "t_tof_ns", "t_rel_ns", "q_pe"):
            if k in d.files:
                out[k] = d[k][pe_slices]
        # adc rows: rebuild per-event row selection

        max_wf = meta.get("max_wf_per_event", 0) or 10**9
        rows_per_ev = np.minimum(np.diff(d["pmt_offsets"]), max_wf).astype(int)
        rows_per_ev = np.minimum(
            rows_per_ev,
            len(d["adc_pmt_ids"]) - np.concatenate(([0], np.cumsum(rows_per_ev)))[:-1],
        )
        row_keep = np.repeat(keep, rows_per_ev)
        for k in ("adc", "adc_pmt_ids"):
            if k in d.files:
                out[k] = d[k][row_keep]
        return out

    for split, indices in idx.items():
        out = subset(indices)
        if split == "test":
            # strip truth: observation only
            truth_keys = [k for k in out if k.startswith("evt_")] + [
                "pmt_ids", "n_pe_pmt", "pe_offsets", "t_emit_ns",
                "t_tof_ns", "t_rel_ns", "q_pe",
            ]
            for k in truth_keys:
                out.pop(k, None)
        np.savez_compressed(blind / f"{split}.npz", **out)
        print(f"wrote blind_task/{split}.npz ({len(indices)} events)")

    # private test truth (full)
    np.savez_compressed(private / "test_full.npz", **subset(idx["test"]))

    # standalone scorer + task sheet into the blind package
    shutil.copy(BENCH / "scripts" / "evaluate.py", blind / "evaluate.py")
    task = f"""# JunoResBench — reconstruction task

You are given digitized PMT waveforms from a JUNO-like liquid-scintillator
toy detector (single 20-inch MCP-PMT type, {meta['n_pmt']} PMTs on a sphere of
R = {meta['radius_m']:.2f} m). Each event is an electron-like energy deposit at an
unknown position/energy/time.

Per event you receive up to {args.max_wf_per_event} digitized channel waveforms
(1 GSa/s, 14-bit, negative pulses on a positive baseline; the stored channels
are a random subset of the hit channels — the number of hit channels per
event is recoverable from `pmt_offsets`).

## Your task

From the waveforms alone, reconstruct per event:
  - visible energy E_rec (MeV)
  - vertex (x_rec, y_rec, z_rec) in meters (detector center = origin)
  - event time t0_rec (ns; the readout window starts at t0 - 300 ns)

## Data

  train.npz  waveforms + truth (calibrate on this)
  val.npz    waveforms + truth
  test.npz   waveforms only (scored)

Prediction format: an npz with keys E_rec, x_rec, y_rec, z_rec, t0_rec
(each length = number of test events). Score with:

  python3 evaluate.py --data <test truth> --pred prediction.npz

Metrics: energy resolution (std of E_rec/E_true), vertex 68% resolution,
timing resolution. Ranking: energy first, then vertex, then timing.
"""
    (blind / "TASK.md").write_text(task)
    print(f"wrote blind_task/TASK.md, evaluate.py; private truth in blind_truth/")


if __name__ == "__main__":
    main()
