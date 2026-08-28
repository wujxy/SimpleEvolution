# junoresbench_static_opt — JunoResBench white-box electron, FULL readout, STATIC detector

Same task family as `junoresbench_full_opt` under the static-detector
convention (the JUNO-MC one): **zero-suppressed full readout** (every
channel that pulsed in the window is digitized — physics-hit and
dark-noise-only channels alike; silent channels are absent) and a
**fixed per-PMT calibration drawn once** (gain 15% / PDE 8% / time
offset 1 ns Gaussian spreads) with stochastic per-PE response. No
environmental drift, no run clock, no `t_run_s`: difficulty comes from
detection physics only (statistics-starved per-PMT calibration from
600 train events, physics-vs-dark pulse separation, trigger-latency
reconstruction). Single-electron population, E ~ U(1, 8) MeV.
Objective: `energy_res` on the labeled val split (lower is better;
ties: vertex, then timing). The judged score is the held-out test
replay against `benchmarks/JunoResBench/blind_truth_electron_static/`.

This is the pure-static re-cut of `electron_full` (whose OU drift
machinery was removed from the generator: environmental effects are a
calibration-operations problem, not detection physics — four of its
five modes were statistically invisible anyway, averaging out over the
~4.7k channels per event).

## Data is NOT in git

The task-package files are multi-GB and stay on disk only (outer
`.gitignore` excludes them). Reproducibility is by seed: see
`benchmarks/JunoResBench/MANIFEST.md` (electron_static section) for the
60-bit seed and sha256 of every file, and regenerate with

```bash
python3 benchmarks/JunoResBench/scripts/make_benchmark.py \
    --name electron_static --events 960 --seed 276263006192544257 \
    --particle-type electron --full-readout --out-format dir \
    --train-events 600 --val-events 120 \
    --max-wf-per-event 0 --optics-mode trace
python3 benchmarks/JunoResBench/scripts/make_whitebox.py --name electron_static
cp -a benchmarks/JunoResBench/whitebox_task_electron_static \
    examples/junoresbench_static_opt/repo/benchmarks/
bash examples/junoresbench_static_opt/setup.sh   # re-init if repo/.git was cleaned
```

## Layout

- `repo/` — the world template (git repo after `setup.sh`; the package
  data are untracked there too): baseline solver `src/solve.py`
  (charge-centroid, memmap-chunked over `adc.npy`), frozen `scripts/`
  (check_verify gate + bench objective), the white-box task package
  under `benchmarks/whitebox_task_electron_static/` (dir-format splits:
  `{meta.json, data.npz, adc.npy}` — memmap-able, shipped meta carries
  the readout contract only: no seed, no detector_config).
- `spec.json` — scientist-mode spec template (creds filled at launch).
- The runtime image (reused from `examples/xsbench_opt/apptainer.sif`)
  and the numpy user-site asset (reused from
  `examples/junoresbench_wb_opt/pyuser/`) are shared with the wb example.

## Metrics contract

Identical to `junoresbench_full_opt`: `verify: PASS` gate, `energy_res`
objective on val, `SANITY` anti-copy floors (0.5% / 3 cm / 0.3 ns),
held-out test judgement host-side.

## Run (singlenode, both arms, no time limit)

```bash
bash examples/junoresbench_static_opt/launch_singlenode.sh scientist \
    runs/singlenode/jrb-static-elec-nolimit-scientist
bash examples/junoresbench_static_opt/launch_singlenode.sh coding \
    runs/singlenode/jrb-static-elec-nolimit-coding
```

"No time limit" = 7-day safety caps (`WALL`, spec `wall_seconds` / steps
3000), not targets.

## Judge afterwards

```bash
python3 scripts/replay_jrb_wb.py \
    --base-repo examples/junoresbench_static_opt/repo \
    --truth benchmarks/JunoResBench/blind_truth_electron_static/test_full.npz \
    --snapshots runs/singlenode/<run>/snapshots \
    --out runs/singlenode/<run>/replay.csv
```

Replay materializes rows by hardlink (the multi-GB frozen side is never
copied per row). Row 0 is the pristine baseline; `replay.test.json` is
the held-out judgement.
