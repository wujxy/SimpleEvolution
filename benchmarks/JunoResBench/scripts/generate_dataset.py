"""Generate a frozen JunoResBench dataset (npz, ragged layout).

Examples:
    # small intermediate-check set with waveforms
    python3 scripts/generate_dataset.py --events 100 --emin 1 --emax 8 \
        --seed 20260901 --out data/test_small.npz

    # truth-only scan (fast, no waveforms) for resolution studies
    python3 scripts/generate_dataset.py --events 10000 --truth-only \
        --seed 20260902 --out data/scan.npz
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.config import DetectorConfig
from benchmarks.JunoResBench.juno_res_bench.detector import DetectorSim
from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout


def sample_vertices(rng, n, r_max):
    """Uniform-in-volume vertices."""
    u = r_max * rng.random(n) ** (1.0 / 3.0)
    ct = rng.uniform(-1, 1, n)
    st = np.sqrt(1 - ct * ct)
    phi = rng.uniform(0, 2 * np.pi, n)
    return np.column_stack((u * st * np.cos(phi), u * st * np.sin(phi), u * ct))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=100)
    ap.add_argument("--emin", type=float, default=1.0)
    ap.add_argument("--emax", type=float, default=8.0)
    ap.add_argument("--rmax", type=float, default=16.0)
    ap.add_argument("--t0-ns", type=float, default=0.0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--layout", choices=["uniform", "juno"], default="uniform")
    ap.add_argument("--n-pmt", type=int, default=17612)
    ap.add_argument("--optics-mode", choices=["fast", "trace"], default="fast",
                    help="stage-3 transport: analytic (fast) or per-photon "
                         "trace (physical timing tails + red shift)")
    ap.add_argument("--truth-only", action="store_true",
                    help="skip waveform synthesis and storage")
    ap.add_argument("--max-wf-per-event", type=int, default=0,
                    help="cap stored waveforms at N random hit channels "
                         "per event (0 = keep all hit channels)")
    ap.add_argument("--skip-per-pe", action="store_true",
                    help="drop per-PE arrays (t_emit/t_tof/t_rel/q_pe); "
                         "keeps only event-level and per-PMT quantities")
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()

    cfg = DetectorConfig(optics_mode=args.optics_mode)
    layout = (
        PMTLayout.from_juno_csv() if args.layout == "juno"
        else PMTLayout.uniform(args.n_pmt, cfg.detector_radius_m)
    )
    sim = DetectorSim(cfg, layout, seed=args.seed)
    rng = np.random.default_rng(args.seed + 1)
    vtxs = sample_vertices(rng, args.events, args.rmax)
    energies = rng.uniform(args.emin, args.emax, args.events)

    out_rows = {k: [] for k in (
        "x_m y_m z_m e_true e_dep e_vis t0 n_gamma n_pe_produced n_pe_total".split()
    )}
    pmt_ids_all, pmt_cnt_all = [], []
    pe = {k: [] for k in ("t_emit_ns t_tof_ns t_rel_ns q_pe".split())}
    adc_blocks = []
    wf_pmt_ids = []

    pmt_off = [0]
    pe_off = [0]
    wf_count = 0
    t_start = time.time()

    for i in range(args.events):
        ev = sim.generate(
            *vtxs[i], float(energies[i]), t0_ns=args.t0_ns,
            with_waveforms=not args.truth_only,
        )
        out_rows["x_m"].append(ev.x_m);   out_rows["y_m"].append(ev.y_m)
        out_rows["z_m"].append(ev.z_m);   out_rows["e_true"].append(ev.e_true_mev)
        out_rows["e_dep"].append(ev.e_dep_mev)
        out_rows["e_vis"].append(ev.e_vis_mev)
        out_rows["t0"].append(ev.t0_ns)
        out_rows["n_gamma"].append(ev.n_gamma)
        out_rows["n_pe_produced"].append(ev.n_pe_produced)
        out_rows["n_pe_total"].append(ev.n_pe_total)

        pmt_ids_all.append(ev.pmt_ids)
        pmt_cnt_all.append(ev.n_pe_pmt)
        if not args.skip_per_pe:
            pe["t_emit_ns"].append(ev.t_emit_ns.astype(np.float32))
            pe["t_tof_ns"].append(ev.t_tof_ns.astype(np.float32))
            pe["t_rel_ns"].append(ev.t_rel_ns.astype(np.float32))
            pe["q_pe"].append(ev.q_pe.astype(np.float32))
        pmt_off.append(pmt_off[-1] + len(ev.pmt_ids))
        pe_off.append(pe_off[-1] + int(ev.n_pe_total))

        if not args.truth_only:
            n_wf = len(ev.adc)
            if args.max_wf_per_event and n_wf > args.max_wf_per_event:
                sel = np.sort(rng.choice(n_wf, args.max_wf_per_event,
                                         replace=False))
                sel_set = set(int(i) for i in sel)
            else:
                sel_set = None
            for k, wf in enumerate(ev.adc):
                if sel_set is not None and k not in sel_set:
                    continue
                adc_blocks.append(wf.astype(np.uint16))
                wf_pmt_ids.append(ev.pmt_ids[k])
                wf_count += 1

        if (i + 1) % max(1, args.events // 10) == 0:
            rate = (i + 1) / (time.time() - t_start)
            print(f"  {i+1}/{args.events} events ({rate:.0f}/s)", flush=True)

    arrays = {
        "pmt_offsets": np.asarray(pmt_off, dtype=np.int64),
        "pe_offsets": np.asarray(pe_off, dtype=np.int64),
        "pmt_ids": np.concatenate(pmt_ids_all).astype(np.int32),
        "n_pe_pmt": np.concatenate(pmt_cnt_all).astype(np.int32),
        **{f"evt_{k}": np.asarray(v) for k, v in out_rows.items()},
        **({k: np.concatenate(v) for k, v in pe.items()}
           if not args.skip_per_pe else {}),
    }
    meta = {
        "detector_config": asdict(cfg),
        "layout": args.layout,
        "n_pmt": int(layout.n_pmt),
        "radius_m": float(layout.radius_m),
        "seed": args.seed,
        "truth_only": args.truth_only,
        "max_wf_per_event": args.max_wf_per_event,
        "skip_per_pe": args.skip_per_pe,
        "waveform_keys": ["adc (uint16, one row per stored channel)",
                          "adc_pmt_ids (int32, PMT id of each adc row)"]
        if not args.truth_only else [],
    }
    arrays["meta"] = np.array(json.dumps(meta, sort_keys=True))

    if not args.truth_only:
        arrays["adc"] = np.stack(adc_blocks) if adc_blocks else np.zeros(
            (0, sim.wave_cfg.n_samples), dtype=np.uint16
        )
        arrays["adc_pmt_ids"] = (
            np.asarray(wf_pmt_ids, dtype=np.int32)
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **arrays)
    size_mb = out.stat().st_size / 1e6
    print(f"wrote {out} ({size_mb:.1f} MB, {args.events} events, "
          f"{wf_count} waveforms)")


if __name__ == "__main__":
    main()
