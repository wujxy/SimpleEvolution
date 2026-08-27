"""Generate a frozen JunoResBench dataset (npz, ragged layout).

Ragged levels: per-PMT (pmt_offsets), per-PE (pe_offsets) and, since the
v1 particle upgrade, per-deposition-step (step_offsets) for gamma/positron
chains. Outer sampling RNG order (seed+1): vertices -> energies ->
particle types -> directions (isotropic mode) — appending new draws at the
end keeps the vertex/energy streams of a given seed stable.

Examples:
    # small intermediate-check set with waveforms, mixed particles
    python3 scripts/generate_dataset.py --events 100 --emin 1 --emax 8 \
        --seed 20261101 --particle-type mixed --direction isotropic \
        --max-wf-per-event 256 --out data/jrb_test_small.npz

    # truth-only scan (fast, no waveforms) for resolution studies
    python3 scripts/generate_dataset.py --events 2000 --truth-only \
        --seed 20261102 --particle-type mixed --direction isotropic \
        --out data/jrb_scan2k.npz
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
from benchmarks.JunoResBench.juno_res_bench.truth import (
    PARTICLE_CODE_TYPE,
    PARTICLE_TYPE_CODE,
    ParticleType,
)


def sample_vertices(rng, n, r_max):
    """Uniform-in-volume vertices."""
    u = r_max * rng.random(n) ** (1.0 / 3.0)
    ct = rng.uniform(-1, 1, n)
    st = np.sqrt(1 - ct * ct)
    phi = rng.uniform(0, 2 * np.pi, n)
    return np.column_stack((u * st * np.cos(phi), u * st * np.sin(phi), u * st * ct))


def sample_isotropic_dirs(rng, n):
    ct = rng.uniform(-1, 1, n)
    st = np.sqrt(1 - ct * ct)
    phi = rng.uniform(0, 2 * np.pi, n)
    return np.column_stack((st * np.cos(phi), st * np.sin(phi), ct))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=100)
    ap.add_argument("--emin", type=float, default=1.0)
    ap.add_argument("--emax", type=float, default=8.0)
    ap.add_argument("--rmax", type=float, default=16.0)
    ap.add_argument("--t0-min", type=float, default=0.0,
                    help="per-event event-time t0 drawn U(t0_min, t0_max) ns "
                         "(the readout window follows the trigger, not t0)")
    ap.add_argument("--t0-max", type=float, default=1000.0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--layout", choices=["uniform", "juno"], default="uniform")
    ap.add_argument("--n-pmt", type=int, default=17612)
    ap.add_argument("--optics-mode", choices=["fast", "trace"], default="fast",
                    help="stage-3 transport: analytic (fast) or per-photon "
                         "trace (physical timing tails + red shift)")
    ap.add_argument("--particle-type",
                    choices=["electron", "gamma", "positron", "mixed"],
                    default="electron",
                    help="event particle mix (mixed draws per event from --mix)")
    ap.add_argument("--mix", type=str, default="1,1,1",
                    help="comma weights electron,gamma,positron for --particle-type mixed")
    ap.add_argument("--direction", choices=["fixed", "isotropic"], default="fixed",
                    help="primary direction: fixed (0,0,1) or per-event isotropic")
    ap.add_argument("--truth-only", action="store_true",
                    help="skip waveform synthesis and storage")
    ap.add_argument("--max-wf-per-event", type=int, default=0,
                    help="cap stored waveforms at N random hit channels "
                         "per event (0 = keep all hit channels)")
    ap.add_argument("--skip-per-pe", action="store_true",
                    help="drop per-PE arrays (t_emit/t_tof/t_rel/q_pe/pe_step); "
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
    # particle types drawn AFTER energies (stream-stable vertices/energies)
    if args.particle_type == "mixed":
        w = np.asarray([float(x) for x in args.mix.split(",")])
        codes = rng.choice(
            [PARTICLE_TYPE_CODE[ParticleType.ELECTRON],
             PARTICLE_TYPE_CODE[ParticleType.GAMMA],
             PARTICLE_TYPE_CODE[ParticleType.POSITRON]],
            size=args.events, p=w / w.sum(),
        )
        types = [PARTICLE_CODE_TYPE[int(c)] for c in codes]
    else:
        types = [ParticleType(args.particle_type)] * args.events
    dirs = (sample_isotropic_dirs(rng, args.events) if args.direction == "isotropic"
            else np.tile(np.array([0.0, 0.0, 1.0]), (args.events, 1)))
    t0s = rng.uniform(args.t0_min, args.t0_max, args.events)

    out_rows = {k: [] for k in (
        "x_m y_m z_m e_true e_dep e_vis e_escaped e_scored particle_type "
        "t0 t_trigger n_gamma n_pe_produced n_pe_total n_steps".split()
    )}
    pmt_ids_all, pmt_cnt_all = [], []
    pe = {k: [] for k in "t_emit_ns t_tof_ns t_rel_ns q_pe pe_step".split()}
    steps = {k: [] for k in
             "step_pos step_e_dep step_e_vis step_t_ns step_dir step_kind".split()}
    adc_blocks = []
    wf_pmt_ids = []

    pmt_off = [0]
    pe_off = [0]
    step_off = [0]
    wf_count = 0
    t_start = time.time()

    for i in range(args.events):
        ev = sim.generate(
            *vtxs[i], float(energies[i]), t0_ns=float(t0s[i]),
            with_waveforms=not args.truth_only,
            direction=tuple(dirs[i]),
            particle_type=types[i],
        )
        out_rows["x_m"].append(ev.x_m);   out_rows["y_m"].append(ev.y_m)
        out_rows["z_m"].append(ev.z_m);   out_rows["e_true"].append(ev.e_true_mev)
        out_rows["e_dep"].append(ev.e_dep_mev)
        out_rows["e_vis"].append(ev.e_vis_mev)
        out_rows["e_escaped"].append(float(ev.e_escape_mev))
        out_rows["e_scored"].append(
            ev.e_true_mev + (1.021998 if ev.particle_type is ParticleType.POSITRON else 0.0)
        )
        out_rows["particle_type"].append(PARTICLE_TYPE_CODE[ev.particle_type])
        out_rows["t0"].append(ev.t0_ns)
        out_rows["t_trigger"].append(float(ev.t_trigger_ns))
        out_rows["n_gamma"].append(ev.n_gamma)
        out_rows["n_pe_produced"].append(ev.n_pe_produced)
        out_rows["n_pe_total"].append(ev.n_pe_total)
        out_rows["n_steps"].append(len(ev.step_e_dep_mev))

        pmt_ids_all.append(ev.pmt_ids)
        pmt_cnt_all.append(ev.n_pe_pmt)
        if not args.skip_per_pe:
            pe["t_emit_ns"].append(ev.t_emit_ns.astype(np.float32))
            pe["t_tof_ns"].append(ev.t_tof_ns.astype(np.float32))
            pe["t_rel_ns"].append(ev.t_rel_ns.astype(np.float32))
            pe["q_pe"].append(ev.q_pe.astype(np.float32))
            pe["pe_step"].append(ev.pe_step.astype(np.int32)
                                 if ev.pe_step is not None else
                                 np.zeros(int(ev.n_pe_total), np.int32))
        pmt_off.append(pmt_off[-1] + len(ev.pmt_ids))
        pe_off.append(pe_off[-1] + int(ev.n_pe_total))
        steps["step_pos"].append(np.asarray(ev.step_pos_m, np.float64))
        steps["step_e_dep"].append(np.asarray(ev.step_e_dep_mev, np.float64))
        steps["step_e_vis"].append(np.asarray(ev.step_e_vis_mev, np.float64))
        steps["step_t_ns"].append(np.asarray(ev.step_t_ns, np.float64))
        steps["step_dir"].append(np.asarray(ev.step_dir, np.float64))
        steps["step_kind"].append(np.asarray(ev.step_kind, np.int8))
        step_off.append(step_off[-1] + len(ev.step_e_dep_mev))

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
        "step_offsets": np.asarray(step_off, dtype=np.int64),
        "pmt_ids": np.concatenate(pmt_ids_all).astype(np.int32),
        "n_pe_pmt": np.concatenate(pmt_cnt_all).astype(np.int32),
        **{f"evt_{k}": (np.asarray(v, np.int8) if k == "particle_type"
                        else np.asarray(v)) for k, v in out_rows.items()},
        **{k: np.concatenate(v) for k, v in steps.items()},
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
        "particle_type": args.particle_type,
        "mix": args.mix if args.particle_type == "mixed" else None,
        "direction": args.direction,
        "t0_range_ns": [args.t0_min, args.t0_max],
        "particle_mix": {p.value: int(sum(1 for t in out_rows["particle_type"]
                                          if t == c))
                         for p, c in PARTICLE_TYPE_CODE.items()},
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
