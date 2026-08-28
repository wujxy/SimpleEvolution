"""Build a frozen JunoResBench benchmark package.

Produces (following the waverec blind-task pattern), for --name NAME:
  data/jrb_NAME[/|.npz]        full dataset with truth (private)
  blind_task_NAME/train|val|test  splits (+ TASK.md, evaluate.py)
  blind_truth_NAME/test_full.npz  private test truth + reference score

Full-readout era (--full-readout --out-format dir):
  zero-suppressed readout (rows = hit ∪ in-window-dark channels) and
  splits as dirs {meta.json, data.npz, adc.npy(memmap-able)}. The detector
  is STATIC (JUNO-MC convention: fixed per-PMT calibration drawn once +
  stochastic response; no environmental drift — that is a calibration-
  operations problem, not detection physics).

Shipped meta carries ONLY the readout contract (layout, n_pmt, radius,
window, waveform keys, readout block) — no seed, no detector_config.
The v1 leak class ("answer written on the riddle": meta constants +
offsets that count hits the waveforms never show) dies here: full-readout
test splits ship waveforms, adc_pmt_ids and wf_offsets only.

Usage:
  python3 scripts/make_benchmark.py --name electron_static --events 960 \
      --seed <60-bit> --particle-type electron --full-readout \
      --out-format dir --train-events 600 --val-events 120
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
    """Split a full dataset into a sub-package (train/val/test).

    Ragged offsets are RE-BASED to the split: sliced arrays start at the
    first kept event, so pmt/pe/step offsets must too. Works on the dict
    shape returned by split_io.load_split (npz or dir format alike);
    `d["adc"]` may be a memmap — fancy row selection materializes only the
    kept rows.
    """

    def __init__(self, d, meta, blind_meta):
        self.d, self.meta, self.blind_meta = d, meta, blind_meta

    def subset(self, indices, strip_seed=False):
        d, meta, blind_meta = self.d, self.meta, self.blind_meta
        n = len(d["evt_e_true"])
        keep = np.zeros(n, bool)
        keep[indices] = True
        keys = [k for k in d.keys() if k not in ("meta", "adc")]
        out = {}
        # event-level arrays
        for k in keys:
            if k in ("pmt_offsets", "pe_offsets", "step_offsets",
                     "wf_offsets"):
                # re-base to the split: cumulative sum of the KEPT events'
                # ragged sizes (indices are sorted, so time order survives;
                # identical to the old start-rebase on contiguous splits,
                # and correct on interleaved ones where it was not)
                counts = np.diff(d[k])[keep]
                out[k] = np.concatenate(([0], np.cumsum(counts)))
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
            if k in keys:
                out[k] = d[k][pmt_slices]
        # ragged per-PE arrays (slice by pe_offsets)
        pe_slices = slices("pe_offsets")
        for k in TRUTH_RAGGED_PE:
            if k in keys:
                out[k] = d[k][pe_slices]
        # ragged per-step arrays (slice by step_offsets)
        step_slices = slices("step_offsets")
        for k in TRUTH_RAGGED_STEP:
            if k in keys:
                out[k] = d[k][step_slices]
        # adc rows: rebuild per-event row selection. wf_offsets (written
        # since the full-readout change) is the authoritative per-event row
        # map; legacy datasets fall back to the pmt_offsets/min(max_wf)
        # arithmetic.
        if "wf_offsets" in keys:
            rows_per_ev = np.diff(d["wf_offsets"]).astype(int)
        else:
            max_wf = meta.get("max_wf_per_event", 0) or 10**9
            rows_per_ev = np.minimum(np.diff(d["pmt_offsets"]), max_wf).astype(int)
            rows_per_ev = np.minimum(
                rows_per_ev,
                len(d["adc_pmt_ids"]) - np.concatenate(([0], np.cumsum(rows_per_ev)))[:-1],
            )
        if d.get("adc") is not None:
            row_keep = np.repeat(keep, rows_per_ev)
            out["adc"] = d["adc"][row_keep]
        if "adc_pmt_ids" in keys:
            out["adc_pmt_ids"] = d["adc_pmt_ids"][np.repeat(keep, rows_per_ev)]
        # full meta travels with every subset dict; the caller decides
        # which meta a shipped file finally carries
        out["meta"] = blind_meta if strip_seed else meta
        return out


def write_split(out_dir: Path, arrays: dict, fmt: str) -> None:
    """Write one split — npz (legacy) or dir {meta.json, data.npz, adc.npy}."""
    if fmt == "dir":
        out_dir.mkdir(parents=True, exist_ok=True)
        meta = arrays.pop("meta")
        adc = arrays.pop("adc", None)
        np.savez_compressed(out_dir / "data.npz", **arrays)
        (out_dir / "meta.json").write_text(
            json.dumps(meta, sort_keys=True, indent=1), encoding="utf-8")
        if adc is not None:
            np.save(out_dir / "adc.npy", np.asarray(adc))
    else:
        meta = arrays.pop("meta")
        arrays["meta"] = np.array(json.dumps(meta, sort_keys=True))
        np.savez_compressed(out_dir.with_suffix(".npz"), **arrays)


def scrub_meta(meta: dict) -> dict:
    """Shipped meta: readout contract only — no seed, no detector_config."""
    from benchmarks.JunoResBench.juno_res_bench._vendor.wavegen_v1 import (
        WaveGenConfig)
    wf = WaveGenConfig()
    keep = ("layout", "n_pmt", "radius_m", "truth_only", "full_readout",
            "max_wf_per_event", "skip_per_pe", "particle_type",
            "mix", "direction", "t0_range_ns", "particle_mix",
            "waveform_keys")
    out = {k: meta[k] for k in keep if k in meta}
    out["seed"] = None
    out["readout"] = {
        "pre_trigger_ns": meta["detector_config"]["pre_trigger_ns"],
        "window_ns": meta["detector_config"]["window_ns"],
        "n_samples": wf.n_samples,
        "sample_interval_ns": wf.sample_interval_ns,
        "adc_bits": wf.adc_bits,
    }
    return out


def build_task_md(particle_type, mix, max_wf_per_event, meta):
    """The task sheet (blind wording). Shared with make_whitebox, which
    prepends a white-box section to the same body."""
    pre = meta["readout"]["pre_trigger_ns"] if "readout" in meta else \
        meta["detector_config"]["pre_trigger_ns"]
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
        data_note = ("""  train  waveforms + truth (calibrate on this;
             evt_particle_type labels the event type — electron-like /
             gamma / positron)""")
        byline = ("Reported overall and per particle type. ")
    else:
        mix_line = (f"Each event is a single {particle_type} at an "
                    "unknown position/energy/time.")
        gamma_note = ""
        e_note = "  - visible energy E_rec (MeV)"
        data_note = "  train  waveforms + truth (calibrate on this)"
        byline = ""
    if meta.get("full_readout"):
        readout_note = f"""Per event you receive the digitized waveforms of
every channel that pulsed in the readout window (zero-suppressed full
readout): physics-hit channels and dark-noise-only channels alike, 1 GSa/s,
14-bit, negative pulses on a positive baseline, 1000 samples each. Silent
channels are not stored — their absence is itself the measurement "quiet".
`wf_offsets` maps events to their adc rows and `adc_pmt_ids` names each
row's channel. Which pulsed channels carry physics photoelectrons and which
carry dark noise is not labeled — that separation is part of the task."""
    else:
        readout_note = f"""Per event you receive up to {max_wf_per_event} digitized channel
waveforms (1 GSa/s, 14-bit, negative pulses on a positive baseline; the
stored channels are a random subset of the hit channels — the number of hit
channels per event is recoverable from `pmt_offsets`)."""
    return f"""# JunoResBench — reconstruction task

You are given digitized PMT waveforms from a JUNO-like liquid-scintillator
toy detector (single 20-inch MCP-PMT type, {meta['n_pmt']} PMTs on a sphere of
R = {meta['radius_m']:.2f} m). {mix_line}
{gamma_note}
## Readout

{readout_note}

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
  val    waveforms + truth
  test   waveforms only (scored; same event population)

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
                    help="package name: data/jrb_<name>, blind_task_<name>/, "
                         "blind_truth_<name>/")
    ap.add_argument("--events", type=int, default=600)
    ap.add_argument("--emin", type=float, default=1.0)
    ap.add_argument("--emax", type=float, default=8.0)
    ap.add_argument("--rmax", type=float, default=16.0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--max-wf-per-event", type=int, default=192)
    ap.add_argument("--full-readout", action="store_true",
                    help="zero-suppressed full readout (hit ∪ dark rows; "
                         "use --max-wf-per-event 0)")
    ap.add_argument("--out-format", choices=["npz", "dir"], default="npz",
                    help="parent + splits format; dir = memmap-able adc.npy")
    ap.add_argument("--train-events", type=int, default=None,
                    help="explicit train size (default 40%%)")
    ap.add_argument("--val-events", type=int, default=None,
                    help="explicit val size (default 20%%)")
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
    ap.add_argument("--reuse", action="store_true",
                    help="skip generation if the parent dataset already "
                         "exists (re-split only; the parent is the "
                         "expensive artifact)")
    args = ap.parse_args()

    # 1. generate the full dataset (private; keeps seed + detector_config)
    full_path = (BENCH / "data" / f"jrb_{args.name}"
                 if args.out_format == "dir"
                 else BENCH / "data" / f"jrb_{args.name}.npz")
    parent_exists = full_path.exists()
    if args.reuse and parent_exists:
        print(f">> reusing {full_path} (re-split only)")
    else:
        cmd = [
            sys.executable, str(BENCH / "scripts" / "generate_dataset.py"),
            "--events", str(args.events), "--emin", str(args.emin),
            "--emax", str(args.emax), "--rmax", str(args.rmax),
            "--seed", str(args.seed), "--layout", args.layout,
            "--max-wf-per-event", str(args.max_wf_per_event),
            "--out-format", args.out_format,
        ]
        for flag in ("full-readout",):
            if getattr(args, flag.replace("-", "_")):
                cmd.append(f"--{flag}")
        cmd += [
            "--optics-mode", args.optics_mode,
            "--particle-type", args.particle_type, "--mix", args.mix,
            "--direction", args.direction,
            "--t0-min", str(args.t0_min), "--t0-max", str(args.t0_max),
            "--out", str(full_path),
        ]
        print(">>", " ".join(cmd))
        subprocess.run(cmd, check=True)

    from benchmarks.JunoResBench.juno_res_bench.split_io import load_split
    d = load_split(full_path, mmap_adc=False)
    meta = d["meta"]
    n = len(d["evt_e_true"])
    if args.train_events is not None:
        n_tr = args.train_events
    else:
        n_tr = int(0.4 * n)
    if args.val_events is not None:
        n_val = args.val_events
    else:
        n_val = int(0.2 * n)
    idx = {"train": np.arange(0, n_tr),
           "val": np.arange(n_tr, n_tr + n_val),
           "test": np.arange(n_tr + n_val, n)}

    blind = BENCH / f"blind_task_{args.name}"
    private = BENCH / f"blind_truth_{args.name}"
    if args.out_format == "dir":
        if blind.exists():
            shutil.rmtree(blind)
    blind.mkdir(exist_ok=True)
    private.mkdir(exist_ok=True)

    blind_meta = scrub_meta(meta)
    subsetter = Subsetter(d, meta, blind_meta)
    subset = subsetter.subset

    for split, indices in idx.items():
        out = subset(indices, strip_seed=True)
        if split == "test":
            # strip truth: observation only. Full-readout era also drops
            # pmt_offsets — the physics-hit-channel identity must come from
            # the waveforms (pulse finding), not from bookkeeping.
            truth_keys = [k for k in out if k.startswith("evt_")] + [
                "pmt_ids", "n_pe_pmt", "pe_offsets", "pe_step",
                "t_emit_ns", "t_tof_ns", "t_rel_ns", "q_pe",
                "step_offsets", *TRUTH_RAGGED_STEP,
            ] + (["pmt_offsets"] if meta.get("full_readout") else [])
            for k in truth_keys:
                out.pop(k, None)
        if args.out_format == "dir":
            write_split(blind / split, out, "dir")
        else:
            write_split(blind / f"{split}.npz", out, "npz")
        print(f"wrote {blind.name}/{split} ({len(indices)} events)")

    # private test truth (full meta + seed kept — never shipped)
    truth = subset(idx["test"])
    truth["meta"] = np.array(json.dumps(meta, sort_keys=True))
    np.savez_compressed(private / "test_full.npz", **truth)

    # standalone scorer + task sheet into the blind package
    shutil.copy(BENCH / "scripts" / "evaluate.py", blind / "evaluate.py")
    task = build_task_md(args.particle_type, args.mix,
                         args.max_wf_per_event, blind_meta)
    (blind / "TASK.md").write_text(task)
    print(f"wrote {blind.name}/TASK.md, evaluate.py; "
          f"private truth in {private.name}/")


if __name__ == "__main__":
    main()
