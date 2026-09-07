#!/usr/bin/env python3
"""Merge ordered shard outputs into the release public/private trees.

Reads shard dirs written by shard_generate.py, concatenates each
population's sparse waveforms and truth in event order, and reproduces
the build_task.build release layout (public/calibration + labels,
public/dev + partial truth, private/final_observations + truth.npz,
oracle threshold + evaluation_config.json for electron_single_site).
The detector geometry file is written from the deterministic layout, not
copied from shards.

Memory discipline: nothing waveform-scale is ever held in RAM. Sample
bytes stream shard->staged file (16 MiB chunks); every index/truth array
streams shard-by-shard into raw byte temps and is assembled into the
final .npz via zip member writes (npy header + byte copy). Peak RSS is
one shard's index block (~100 MB) plus the small per-event truth keys.
"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    JUNO_LPMT_CSV,
    JUNO_LPMT_TYPE_CSV,
)
from benchmarks.JunoResBench.world_generator.oracle_vertex import (
    charge_pattern_vertex_rms,
    freeze_gate,
    freeze_threshold,
)
from benchmarks.JunoResBench.world_generator.build_task import (
    DEV_EVENTS,
    ENERGY_BIAS_1MEV_MAX,
    ENERGY_BIAS_MAX,
    ENERGY_RESOLUTION_GATE,
    VERTEX_HIGH_ENERGY_RMS_RATIO_MAX,
    VERTEX_RADIAL_BIAS_MAX_M,
    select_layout,
)
from benchmarks.JunoResBench.world_generator.populations import calibration_population, physics_population

INDEX_KEYS = (
    ("event_segment_offsets", "<i8"),
    ("segment_sample_offsets", "<i8"),
    ("segment_pmt_ids", "<i4"),
    ("segment_start_samples", "<i2"),
)
TRUTH_STREAM_KEYS = ("step_offsets",)  # rebased; step payload keys collected below


def _shard_roots(shards_root):
    return sorted(
        (p for p in Path(shards_root).iterdir()
         if p.is_dir() and p.name.startswith("shard_")),
        key=lambda p: int(p.name.split("_")[1]),
    )


def _copy_member(zf, name, descr, shape, raw_path, chunk=1 << 22):
    """Assemble one .npy member into the npz from a raw-byte temp file."""
    import shutil
    with zf.open(f"{name}.npy", "w") as member:
        np.lib.format.write_array_header_2_0(member, {
            "descr": descr, "fortran_order": False, "shape": shape})
        with open(raw_path, "rb") as src:
            shutil.copyfileobj(src, member, length=chunk)


def merge_split(shard_roots, name, merged_dir, prune=False, truth_out=None):
    """Stream-merge shards of one population into merged_dir (sparse layout)."""
    import shutil
    import zipfile

    roots = [r for r in shard_roots if (r / name).exists()]
    missing = [r.name for r in shard_roots if not (r / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"population {name} missing from shards {missing[:8]}"
            f"{'...' if len(missing) > 8 else ''} — shards incomplete; "
            f"refusing to produce a partial release")

    # ---- pass 1: totals only (index + step counts); nothing large retained ----
    n_events = n_segments = n_samples = n_steps = 0
    meta = None
    step_keys = None
    step_info = None   # dtype + trailing dims, captured pre-prune (shard_0 dies in pass 2)
    for root in roots:
        src = root / name
        with np.load(src / "index.npz", allow_pickle=False) as ix:
            n_events += len(ix["event_segment_offsets"]) - 1
            n_segments += len(ix["segment_pmt_ids"])
            n_samples += int(ix["segment_sample_offsets"][-1])
        with np.load(src / "truth.npz", allow_pickle=False) as t:
            n_steps += int(t["step_offsets"][-1])
            if step_keys is None:
                step_keys = sorted(k for k in t.files if k.startswith("step_"))
                step_info = {k: (t[k].dtype.str, t[k].shape[1:]) for k in step_keys}
            else:
                assert sorted(k for k in t.files if k.startswith("step_")) == step_keys
        meta = meta or json.loads((src / "metadata.json").read_text())

    merged_dir.mkdir(parents=True, exist_ok=True)
    samples_target = (merged_dir / "segment_samples.npy").open("wb")
    np.lib.format.write_array_header_2_0(samples_target, {
        "descr": "<i2", "fortran_order": False, "shape": (n_samples,)})
    raw_files = {k: (merged_dir / f".{k}.tmp").open("wb")
                 for k, _ in INDEX_KEYS}
    for k in step_keys:
        raw_files[k] = (merged_dir / f".{k}.tmp").open("wb")
    truth_small = {}   # per-event truth keys, small enough for RAM
    for key in ("event_segment_offsets", "segment_sample_offsets", "step_offsets"):
        raw_files[key].write(np.zeros(1, "<i8").tobytes())
    seg_base = samp_base = step_base = 0

    # ---- pass 2: stream every shard ----
    for root in roots:
        src = root / name
        with np.load(src / "index.npz", allow_pickle=False) as ix:
            ev_off = ix["event_segment_offsets"]
            s_off = ix["segment_sample_offsets"]
            ids = ix["segment_pmt_ids"]
            starts = ix["segment_start_samples"]
            raw_files["event_segment_offsets"].write(
                (ev_off[1:] + seg_base).astype("<i8").tobytes())
            raw_files["segment_sample_offsets"].write(
                (s_off[1:] + samp_base).astype("<i8").tobytes())
            raw_files["segment_pmt_ids"].write(ids.astype("<i4").tobytes())
            raw_files["segment_start_samples"].write(starts.astype("<i2").tobytes())
        with open(src / "segment_samples.npy", "rb") as fh:
            # verify byte-completeness, then append straight into the
            # merged npy — no staged copy, so peak disk is one payload
            version = np.lib.format.read_magic(fh)
            if version == (1, 0):
                shape, _, _ = np.lib.format.read_array_header_1_0(fh)
            elif version == (2, 0):
                shape, _, _ = np.lib.format.read_array_header_2_0(fh)
            else:
                raise IOError(f"unsupported npy version {version}")
            data_start = fh.tell()
            expected = shape[0] * np.dtype("<i2").itemsize
            actual = (src / "segment_samples.npy").stat().st_size - data_start
            if actual != expected or shape[0] != int(s_off[-1] - s_off[0]):
                samples_target.close()
                (merged_dir / "segment_samples.npy").unlink()
                raise IOError(
                    f"truncated shard {root.name}/{name}: file holds {actual} "
                    f"sample bytes, header declares {expected}, index declares "
                    f"{int(s_off[-1] - s_off[0]) * 2} — regenerate this shard")
            shutil.copyfileobj(fh, samples_target, length=1 << 24)
        with np.load(src / "truth.npz", allow_pickle=False) as t:
            for key in t.files:
                if key.startswith("step_"):
                    continue
                truth_small.setdefault(key, []).append(t[key])
            counts = np.diff(t["step_offsets"])
            off = (step_base + np.cumsum(counts, dtype=np.int64)).astype("<i8")
            raw_files["step_offsets"].write(off.tobytes())
            for key in step_keys:
                if key != "step_offsets":
                    raw_files[key].write(t[key].tobytes())
            step_base = int(off[-1]) if len(off) else step_base
        seg_base += int(ev_off[-1])
        samp_base += int(s_off[-1])
        if prune:
            shutil.rmtree(src)
    samples_target.close()
    for fh in raw_files.values():
        fh.close()

    # ---- assemble the split ----
    shapes = {"event_segment_offsets": (n_events + 1,),
              "segment_sample_offsets": (n_segments + 1,),
              "segment_pmt_ids": (n_segments,),
              "segment_start_samples": (n_segments,)}
    with zipfile.ZipFile(merged_dir / "index.npz", "w", zipfile.ZIP_DEFLATED) as zf:
        for key, descr in INDEX_KEYS:
            _copy_member(zf, key, descr, shapes[key], merged_dir / f".{key}.tmp")
    for key, descr in INDEX_KEYS:
        (merged_dir / f".{key}.tmp").unlink()
    (merged_dir / "metadata.json").write_text(
        json.dumps({**meta, "n_events": n_events}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    # ---- truth: npz with streamed step arrays + small event arrays ----
    if truth_out is not None:
        step_shapes = {k: (n_events + 1,) if k == "step_offsets"
                       else (n_steps, *step_info[k][1])
                       for k in step_keys}
        with zipfile.ZipFile(truth_out, "w", zipfile.ZIP_DEFLATED) as zf:
            step_descr = {k: ("<i8" if k == "step_offsets" else step_info[k][0])
                          for k in step_keys}
            for k in step_keys:
                _copy_member(zf, k, step_descr[k], step_shapes[k],
                             merged_dir / f".{k}.tmp")
                (merged_dir / f".{k}.tmp").unlink()
            for key, blocks in truth_small.items():
                with zf.open(f"{key}.npy", "w") as member:
                    arr = np.concatenate(blocks)
                    np.lib.format.write_array_header_2_0(member, {
                        "descr": arr.dtype.str, "fortran_order": False,
                        "shape": arr.shape})
                    member.write(arr.tobytes())
    else:
        for k in step_keys:
            (merged_dir / f".{k}.tmp").unlink(missing_ok=True)
    return {k: np.concatenate(b) for k, b in truth_small.items()}


def publish_shards(shard_roots, out, args, populations, config, layout):
    """Zero-copy publication: waveform shards stay exactly where they are.

    Writes only the small bookkeeping into `out`: concatenated private truth,
    calibration labels, evaluation_config, oracle record, and per-population
    manifests (ordered shard entries with sha256 of every waveform payload,
    which doubles as the byte-completeness check).
    """
    import hashlib

    public = out / "public"
    private = out / "private"
    populations = {
        "calibration": populations["calibration"],
        "dev": populations["dev"],
        "final": populations["final"],
    }
    manifest = {}
    for name, population in populations.items():
        entries = []
        truths = []
        for root in shard_roots:
            src = root / name
            index_path = src / "index.npz"
            if not index_path.exists():
                continue
            with np.load(index_path, allow_pickle=False) as ix:
                events = int(len(ix["event_segment_offsets"]) - 1)
                declared = int(ix["segment_sample_offsets"][-1])
            payload = src / "segment_samples.npy"
            digest = hashlib.sha256()
            payload_bytes = 0
            with payload.open("rb") as fh:
                version = np.lib.format.read_magic(fh)
                if version == (1, 0):
                    shape, _, _ = np.lib.format.read_array_header_1_0(fh)
                elif version == (2, 0):
                    shape, _, _ = np.lib.format.read_array_header_2_0(fh)
                else:
                    raise IOError(f"unsupported npy version {version}")
                data_start = fh.tell()
                for block in iter(lambda: fh.read(1 << 24), b""):
                    digest.update(block)
                    payload_bytes += len(block)
            expected_bytes = shape[0] * 2
            if payload_bytes != expected_bytes or shape[0] != declared:
                raise IOError(
                    f"{src}: payload holds {payload_bytes} bytes, npy header "
                    f"declares {expected_bytes}, index declares {declared * 2}"
                    " — regenerate this shard")
            shard_truth = src / "truth.npz"
            if shard_truth.exists():
                with np.load(shard_truth, allow_pickle=False) as data:
                    truths.append({k: data[k] for k in data.files})
            entries.append({
                "shard": root.name,
                "index": str(index_path.resolve()),
                "samples": str(payload.resolve()),
                "truth": str(shard_truth.resolve()),
                "events": events,
                "declared_samples": declared,
                "sha256": digest.hexdigest(),
            })
        total = sum(e["events"] for e in entries)
        expected = len(population["evt_e_true"])
        assert total == expected, f"{name}: shards hold {total}/{expected} events"
        manifest[name] = {"n_events": expected, "shards": entries}

    # final truth: concatenated in shard order (small per-event arrays only)
    final_truth = {}
    step_blocks = []
    step_base = 0
    for block in manifest["final"]["shards"]:
        with np.load(block["truth"], allow_pickle=False) as data:
            for key in data.files:
                arr = data[key]
                if key == "step_offsets":  # per-shard offsets restart at 0
                    step_blocks.append(arr[:-1] + step_base)
                    step_base += int(arr[-1])
                    continue
                final_truth.setdefault(key, []).append(arr)
    # drop each shard's terminal boundary while concatenating, then close the
    # stream with one final boundary so len(step_offsets) == n_events + 1
    final_truth["step_offsets"] = [np.concatenate(
        step_blocks + [np.array([step_base], dtype=step_blocks[0].dtype)])]
    np.savez_compressed(
        private / "truth.npz",
        **{k: np.concatenate(v) for k, v in final_truth.items()},
    )
    (public / "calibration").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        public / "calibration" / "labels.npz",
        source_energy_mev=populations["calibration"]["evt_e_true"],
        deployment_position_m=populations["calibration"]["evt_vertex_m"],
    )

    evaluation = {
        "energy_target_r_1mev": 0.03,
        "energy_resolution_gate": ENERGY_RESOLUTION_GATE,
        "energy_bias_1mev_abs_max": ENERGY_BIAS_1MEV_MAX,
        "energy_bias_abs_max": ENERGY_BIAS_MAX,
        "vertex_radial_bias_abs_max_m": VERTEX_RADIAL_BIAS_MAX_M,
        "vertex_high_energy_rms_ratio_max": VERTEX_HIGH_ENERGY_RMS_RATIO_MAX,
    }
    if args.task == "electron_single_site":
        final = populations["final"]
        vertices = final["evt_vertex_m"][(final["evt_sample_role"] == 0) & (final["evt_e_true"] == 1.0)]
        oracle_rms = charge_pattern_vertex_rms(vertices, layout, config)
        reference = freeze_threshold(oracle_rms)
        gate = freeze_gate(oracle_rms)
        (private / "electron_oracle.json").write_text(
            json.dumps({"method": "ideal_charge_pattern_fisher",
                        "oracle_vertex_rms_m": oracle_rms,
                        "vertex_rms_reference_m": reference,
                        "vertex_resolution_gate_m": gate}, indent=2) + "\n",
            encoding="utf-8")
        evaluation["vertex_rms_reference_m"] = reference
        evaluation["vertex_resolution_gate_m"] = gate
    (public / "evaluation_config.json").write_text(
        json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    (public / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (private / "final_shards.json").write_text(
        json.dumps(manifest["final"], indent=1) + "\n", encoding="utf-8")

    # symlink the in-place shards into one release tree: readers may either
    # follow MANIFEST paths or walk <root>/<population>/shard_*/ directly
    roots = {"calibration": public / "calibration",
             "dev": public / "dev",
             "final": private / "final"}
    for name, population in manifest.items():
        link_root = roots[name]
        link_root.mkdir(parents=True, exist_ok=True)
        for e in population["shards"]:
            (link_root / e["shard"]).symlink_to(
                Path(e["samples"]).parent, target_is_directory=True)
    print(f"published {len(shard_roots)} shards in place -> {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--geometry-mode", choices=("juno", "uniform"), default="juno")
    parser.add_argument("--n-pmt", type=int, default=None,
                        help="only used with --geometry-mode uniform")
    parser.add_argument("--calibration-events-per-point", type=int, default=20)
    parser.add_argument("--probe-events-per-point", type=int, default=200)
    parser.add_argument("--controls", type=int, default=7680)
    parser.add_argument("--shards-root", required=True, help="dir containing shard_*/ subdirs")
    parser.add_argument("--out", required=True, help="release output root (must be fresh)")
    parser.add_argument("--dev-out", default=None,
                        help="separate root for the dev split (quota split across disks); "
                             "defaults to <out>/public/dev")
    parser.add_argument("--prune-shards", action="store_true",
                        help="delete each shard's population dir right after merging it "
                             "(disk-budget mode: merged release + shards never coexist in full)")
    parser.add_argument("--publish-shards", action="store_true",
                        help="zero-copy publication: keep waveform shards in place, write "
                             "only truth/labels/config/oracle/manifests into --out")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output: {out}")

    shard_roots = _shard_roots(args.shards_root)
    manifests = [json.loads((p / "shard_manifest.json").read_text()) for p in shard_roots]
    assert all(m["seed"] == args.seed and m["task"] == args.task for m in manifests), "shard seed/task mismatch"
    covered = {}
    for m in manifests:
        for name, (lo, hi, _n) in m["splits"].items():
            a, b = covered.get(name, (0, 0))
            covered[name] = (max(a, hi), b + (hi - lo))
    streams = np.random.SeedSequence(args.seed).spawn(5)
    seeds = [int(s.generate_state(1, dtype=np.uint64)[0]) for s in streams]
    calibration = calibration_population(seeds[0], args.calibration_events_per_point)
    dev = physics_population(args.task, seeds[1], args.probe_events_per_point, args.controls)
    dev = {key: value[:DEV_EVENTS] for key, value in dev.items()}
    final = physics_population(args.task, seeds[2], args.probe_events_per_point, args.controls)
    expected = {"calibration": calibration, "dev": dev, "final": final}
    for name, population in expected.items():
        total = len(population["evt_e_true"])
        hi, n = covered.get(name, (0, 0))
        assert hi == total and n == total, f"{name}: shards cover {n}/{total} events (max index {hi})"

    public = out / "public"
    private = out / "private"
    public.mkdir(parents=True)
    private.mkdir()

    config = DetectorConfig(optics_mode="trace", full_readout=True, three_gamma_frac=0.0)
    layout = select_layout(
        args.geometry_mode, args.n_pmt, JUNO_LPMT_CSV, JUNO_LPMT_TYPE_CSV
    )

    if args.publish_shards:
        np.savez_compressed(
            public / "detector_geometry.npz",
            pmt_positions_m=layout.positions_m,
            pmt_copy_no=layout.copy_no,
            pmt_model=layout.pmt_model,
        )
        publish_shards(shard_roots, out, args,
                       {"calibration": calibration, "dev": dev, "final": final},
                       config, layout)
        return

    np.savez_compressed(
        public / "detector_geometry.npz",
        pmt_positions_m=layout.positions_m,
        pmt_copy_no=layout.copy_no,
        pmt_model=layout.pmt_model,
    )

    merge_split(shard_roots, "calibration", public / "calibration",
                prune=args.prune_shards)
    np.savez_compressed(
        public / "calibration" / "labels.npz",
        source_energy_mev=calibration["evt_e_true"],
        deployment_position_m=calibration["evt_vertex_m"],
    )

    # dev: unlabeled real data — shards' dev truth is merged but not published
    dev_root = Path(args.dev_out) if args.dev_out else out
    (dev_root / "public").mkdir(parents=True, exist_ok=True)
    merge_split(shard_roots, "dev", dev_root / "public" / "dev",
                prune=args.prune_shards)

    merge_split(shard_roots, "final", private / "final",
                prune=args.prune_shards, truth_out=private / "truth.npz")

    evaluation = {
        "energy_target_r_1mev": 0.03,
        "energy_resolution_gate": ENERGY_RESOLUTION_GATE,
        "energy_bias_1mev_abs_max": ENERGY_BIAS_1MEV_MAX,
        "energy_bias_abs_max": ENERGY_BIAS_MAX,
        "vertex_radial_bias_abs_max_m": VERTEX_RADIAL_BIAS_MAX_M,
        "vertex_high_energy_rms_ratio_max": VERTEX_HIGH_ENERGY_RMS_RATIO_MAX,
    }
    if args.task == "electron_single_site":
        vertices = final["evt_vertex_m"][(final["evt_sample_role"] == 0) & (final["evt_e_true"] == 1.0)]
        oracle_rms = charge_pattern_vertex_rms(vertices, layout, config)
        reference = freeze_threshold(oracle_rms)
        gate = freeze_gate(oracle_rms)
        (private / "electron_oracle.json").write_text(
            json.dumps({"method": "ideal_charge_pattern_fisher",
                        "oracle_vertex_rms_m": oracle_rms,
                        "vertex_rms_reference_m": reference,
                        "vertex_resolution_gate_m": gate}, indent=2) + "\n",
            encoding="utf-8")
        evaluation["vertex_rms_reference_m"] = reference
        evaluation["vertex_resolution_gate_m"] = gate
    (public / "evaluation_config.json").write_text(
        json.dumps(evaluation, indent=2) + "\n", encoding="utf-8")
    print(f"merged {len(shard_roots)} shards -> {out}")


if __name__ == "__main__":
    main()
