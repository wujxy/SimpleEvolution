"""Build a frozen JunoResBench benchmark package.

Produces (following the waverec blind-task pattern), for --name NAME:
  data/jrb_NAME.npz             full dataset with truth (private)
  blind_task_NAME/train.npz     truth visible (agent calibrates on it)
  blind_task_NAME/val.npz       truth visible
  blind_task_NAME/test.npz      adc + geometry only, meta/truth stripped
  blind_task_NAME/TASK.md       the task sheet given to agents
  blind_task_NAME/evaluate.py   standalone scorer (numpy only)
  blind_truth_NAME/test_full.npz  private test truth + reference score

Trigger architecture (v4): the readout window follows a global trigger on
the PE rate (see juno_res_bench/stages/s5_electronics.py), so the scored
event time is measured from the window start (t0_ref = evt_t0 -
(evt_t_trigger - pre_trigger_ns)). Per-event t0 is drawn U(t0_min, t0_max).

Particle content: --particle-type mixed gives an electron/gamma/positron
sample (equal thirds by default; train/val carry evt_particle_type so
solvers can learn type-conditional calibration); --particle-type electron
gives the electron-only package. The blind-package meta drops the
generation seed (fairness: seed + code would regenerate the test truth);
blind_truth keeps it.

Usage:
  python3 scripts/make_benchmark.py --name electron --events 600 \
      --seed 588010011806800290 --particle-type electron
  python3 scripts/make_benchmark.py --name mixed --events 600 \
      --seed 258797109207854889
(seeds are large random values: the white-box packages ship the generator,
so seed search must be computationally dead)
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BENCH = Path(__file__).resolve().parents[1]

TRUTH_RAGGED_PMT = ("pmt_ids", "n_pe_pmt")
TRUTH_RAGGED_PE = ("t_emit_ns", "t_tof_ns", "t_rel_ns", "q_pe", "pe_step")
TRUTH_RAGGED_STEP = ("step_pos", "step_e_dep", "step_e_vis", "step_t_ns",
                     "step_dir", "step_kind")


class Subsetter:
    """Split a full dataset npz into a sub-package (train/val/test).

    Ragged offsets are RE-BASED to the split: sliced arrays start at the
    first kept event, so pmt/pe/step offsets must too (train starts at 0 by
    construction; val/test do NOT — unrebased offsets would index into the
    full-dataset arrays).
    """

    def __init__(self, d, meta, blind_meta):
        self.d, self.meta, self.blind_meta = d, meta, blind_meta

    def subset(self, indices, strip_seed=False):
        d, meta, blind_meta = self.d, self.meta, self.blind_meta
        n = len(d["evt_e_true"])
        keep = np.zeros(n, bool)
        keep[indices] = True
        out = {}
        # event-level arrays
        for k in d.files:
            if k == "meta":
                out[k] = np.array(json.dumps(
                    blind_meta if strip_seed else meta, sort_keys=True))
            elif k in ("pmt_offsets", "pe_offsets", "step_offsets"):
                # re-base AND re-end: starts of kept events plus the end of
                # the LAST kept event (d[k][-1] would be the full-dataset
                # total whenever the split is not the tail)
                kept = np.flatnonzero(keep)
                base = int(d[k][kept[0]])
                last = int(d[k][kept[-1] + 1])
                out[k] = np.append(d[k][:-1][keep] - base, last - base)
            elif k.startswith("evt_"):
                out[k] = d[k][keep]
        sel_ev = np.where(keep)[0]

        def slices(offsets_key):
            return np.concatenate(
                [np.arange(d[offsets_key][i], d[offsets_key][i + 1])
                 for i in sel_ev]
            ) if len(sel_ev) else np.zeros(0, int)

        # ragged per-PMT arrays (slice by pmt_offsets)
        pmt_slices = slices("pmt_offsets")
        for k in TRUTH_RAGGED_PMT:
            if k in d.files:
                out[k] = d[k][pmt_slices]
        # ragged per-PE arrays (slice by pe_offsets)
        pe_slices = slices("pe_offsets")
        for k in TRUTH_RAGGED_PE:
            if k in d.files:
                out[k] = d[k][pe_slices]
        # ragged per-step arrays (slice by step_offsets)
        step_slices = slices("step_offsets")
        for k in TRUTH_RAGGED_STEP:
            if k in d.files:
                out[k] = d[k][step_slices]
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


def build_task_md(particle_type, mix, max_wf_per_event, meta):
    """The task sheet (blind wording). Shared with make_whitebox, which
    prepends a white-box section to the same body."""
    pre = meta["detector_config"]["pre_trigger_ns"]
    if particle_type == "mixed" and mix == "1,1,1":
        mix_line = ("Each event is an equal mix of electron, gamma and "
                    "positron events at an unknown position/energy/time.")
        gamma_note = ("""Gammas deposit energy through a Compton chain
(multi-point, ~cm vertex spread, slightly stronger light quenching);
positrons add 2 x 511 keV annihilation gammas (with a ~3 ns delayed
ortho-positronium component for ~55% of annihilations).
""")
        e_note = ("  - visible energy E_rec (MeV; for positrons this includes "
                  "the annihilation light — the scored reference is\n"
                  "    E_kin + 1.022 MeV, the JUNO convention)")
        data_note = ("""  train.npz  waveforms + truth (calibrate on this;
             evt_particle_type labels the event type — electron-like /
             gamma / positron)""")
        byline = ("Reported overall and per particle type. ")
    else:
        mix_line = (f"Each event is a single {particle_type} at an "
                    "unknown position/energy/time.")
        gamma_note = ""
        e_note = "  - visible energy E_rec (MeV)"
        data_note = "  train.npz  waveforms + truth (calibrate on this)"
        byline = ""
    return f"""# JunoResBench — reconstruction task

You are given digitized PMT waveforms from a JUNO-like liquid-scintillator
toy detector (single 20-inch MCP-PMT type, {meta['n_pmt']} PMTs on a sphere of
R = {meta['radius_m']:.2f} m). {mix_line}
{gamma_note}
## Readout

Per event you receive up to {max_wf_per_event} digitized channel
waveforms (1 GSa/s, 14-bit, negative pulses on a positive baseline; the
stored channels are a random subset of the hit channels — the number of hit
channels per event is recoverable from `pmt_offsets`).

The readout window is defined by the detector's global trigger: sample 0 of
every waveform sits at (trigger time - {pre:.0f} ns), and the window is
1000 ns long. The trigger fires on the event itself (a fixed charge
threshold on the summed detector rate), so its timing jitters event-by-event
with vertex position, light-collection statistics and dark noise. Waveforms
also contain uncorrelated dark-noise pulses; the per-PE truth arrays in
train/val cover physics photoelectrons only (dark pulses are in the
waveforms but not in the truth lists).

## Your task

From the waveforms alone, reconstruct per event:
{e_note}
  - vertex (x_rec, y_rec, z_rec) in meters (detector center = origin)
  - event time t0_rec in ns, **measured from the window start** (sample 0 =
    trigger time - {pre:.0f} ns). This is the time the scintillation light
    was emitted; recovering it means correcting the trigger latency, which
    depends on the vertex — the two tasks are coupled.

## Data

{data_note}
  val.npz    waveforms + truth
  test.npz   waveforms only (scored; same event population)

Prediction format: an npz with keys E_rec, x_rec, y_rec, z_rec, t0_rec
(each length = number of test events). Score with:

  python3 evaluate.py --data <test truth> --pred prediction.npz

Metrics: energy resolution ((q84-q16)/2 of E_rec/E_ref — quantile width,
gamma escape tails count), vertex 68% resolution, timing resolution
((q84-q16)/2 of t0_rec - t0_ref, ns). {byline}Ranking: energy first, then
vertex, then timing.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True,
                    help="package name: data/jrb_<name>.npz, blind_task_<name>/, "
                         "blind_truth_<name>/")
    ap.add_argument("--events", type=int, default=600)
    ap.add_argument("--emin", type=float, default=1.0)
    ap.add_argument("--emax", type=float, default=8.0)
    ap.add_argument("--rmax", type=float, default=16.0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--max-wf-per-event", type=int, default=192)
    ap.add_argument("--layout", choices=["uniform", "juno"], default="uniform")
    ap.add_argument("--optics-mode", choices=["fast", "trace"], default="trace",
                    help="trace = physical propagation tails + red shift")
    ap.add_argument("--particle-type",
                    choices=["electron", "gamma", "positron", "mixed"],
                    default="electron")
    ap.add_argument("--mix", type=str, default="1,1,1",
                    help="comma weights electron,gamma,positron (mixed)")
    ap.add_argument("--direction", choices=["fixed", "isotropic"],
                    default="isotropic")
    ap.add_argument("--t0-min", type=float, default=0.0)
    ap.add_argument("--t0-max", type=float, default=1000.0)
    args = ap.parse_args()

    # 1. generate the full dataset
    full_path = BENCH / "data" / f"jrb_{args.name}.npz"
    cmd = [
        sys.executable, str(BENCH / "scripts" / "generate_dataset.py"),
        "--events", str(args.events), "--emin", str(args.emin),
        "--emax", str(args.emax), "--rmax", str(args.rmax),
        "--seed", str(args.seed), "--layout", args.layout,
        "--max-wf-per-event", str(args.max_wf_per_event),
        "--optics-mode", args.optics_mode,
        "--particle-type", args.particle_type, "--mix", args.mix,
        "--direction", args.direction,
        "--t0-min", str(args.t0_min), "--t0-max", str(args.t0_max),
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

    blind = BENCH / f"blind_task_{args.name}"
    private = BENCH / f"blind_truth_{args.name}"
    blind.mkdir(exist_ok=True)
    private.mkdir(exist_ok=True)

    blind_meta = dict(meta)
    blind_meta["seed"] = None          # fairness: seed + code = test truth
    subsetter = Subsetter(d, meta, blind_meta)
    subset = subsetter.subset

    for split, indices in idx.items():
        out = subset(indices, strip_seed=True)
        if split == "test":
            # strip truth: observation only
            truth_keys = [k for k in out if k.startswith("evt_")] + [
                "pmt_ids", "n_pe_pmt", "pe_offsets", "pe_step",
                "t_emit_ns", "t_tof_ns", "t_rel_ns", "q_pe",
                "step_offsets", *TRUTH_RAGGED_STEP,
            ]
            for k in truth_keys:
                out.pop(k, None)
        np.savez_compressed(blind / f"{split}.npz", **out)
        print(f"wrote {blind.name}/{split}.npz ({len(indices)} events)")

    # private test truth (full, seed kept)
    np.savez_compressed(private / "test_full.npz", **subset(idx["test"]))

    # standalone scorer + task sheet into the blind package
    shutil.copy(BENCH / "scripts" / "evaluate.py", blind / "evaluate.py")
    task = build_task_md(args.particle_type, args.mix,
                         args.max_wf_per_event, meta)
    (blind / "TASK.md").write_text(task)
    print(f"wrote {blind.name}/TASK.md, evaluate.py; "
          f"private truth in {private.name}/")


if __name__ == "__main__":
    main()
