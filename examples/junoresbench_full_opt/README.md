# junoresbench_full_opt — JunoResBench white-box reconstruction, FULL readout

Same task family as `junoresbench_wb_opt`, but the readout is opened
all the way: **zero-suppressed full readout** (every channel that pulsed
in the window is digitized — physics-hit and dark-noise-only channels
alike; silent channels are absent), and **calibration drift runs on the
dataset clock** (per-PMT gain/PDE OU wander + global PDE/dark-rate
modes; no per-event common mode — that is degenerate with energy).
Splits interleave through the run so train labels span the drift
history. Single-electron population, E ~ U(1, 8) MeV. Objective:
`energy_res` on the labeled val split (lower is better; ties: vertex,
then timing). The judged score is the held-out test replay against
`benchmarks/JunoResBench/blind_truth_electron_full/`.

This is the realism arm of the electron task. The 192-channel subsampled
variant collapsed to an information-limited regime (energy via the leaked
N_hit, vertex on the 192-channel Cramér–Rao bound) in ~1.5 h; here the
information is complete and the difficulty is calibration: per-PMT
calibration from 600 train events is statistics-starved (per-PMT PDE
spread 8% vs ~7% estimation error), pulse finding must separate physics
PEs from dark noise, and drifting calibrations reward a time-aware
pipeline over one static fit.

## Data is NOT in git

The task-package npz files are multi-GB and stay on disk only (outer
`.gitignore` excludes them). Reproducibility is by seed: see
`benchmarks/JunoResBench/MANIFEST.md` (electron_full section) for the
60-bit seed and sha256 of every file, and regenerate with

```bash
python3 benchmarks/JunoResBench/scripts/make_benchmark.py \
    --name electron_full --events 960 --seed 1128258127999576769 \
    --particle-type electron --full-readout --drift --out-format dir \
    --interleave --train-events 600 --val-events 120 \
    --max-wf-per-event 0 --optics-mode trace
python3 benchmarks/JunoResBench/scripts/make_whitebox.py --name electron_full
cp -a benchmarks/JunoResBench/whitebox_task_electron_full \
    examples/junoresbench_full_opt/repo/benchmarks/
bash examples/junoresbench_full_opt/setup.sh   # re-init if repo/.git was cleaned
```

## Layout

- `repo/` — the world template (git repo after `setup.sh`; the package
  data are untracked there too): baseline solver `src/solve.py`
  (charge-centroid, memmap-chunked over `adc.npy`), frozen `scripts/`
  (check_verify gate + bench objective), the white-box task package
  under `benchmarks/whitebox_task_electron_full/` (dir-format splits:
  `{meta.json, data.npz, adc.npy}` — memmap-able, shipped meta carries
  the readout contract only: no seed, no detector_config).
- `spec.json` — scientist-mode spec template (creds filled at launch).
- The runtime image (reused from `examples/xsbench_opt/apptainer.sif`)
  and the numpy user-site asset (reused from
  `examples/junoresbench_wb_opt/pyuser/`) are shared with the wb example.

## Metrics contract

Identical to `junoresbench_wb_opt`: `verify: PASS` gate, `energy_res`
objective on val, `SANITY` anti-copy floors (0.5% / 3 cm / 0.3 ns),
held-out test judgement host-side. The generator change adding
`full_readout` is byte-stable for the default mode (regression-tested).

## Run (singlenode, both arms, no time limit)

```bash
bash examples/junoresbench_full_opt/launch_singlenode.sh scientist \
    runs/singlenode/jrb-full-elec-nolimit-scientist
bash examples/junoresbench_full_opt/launch_singlenode.sh coding \
    runs/singlenode/jrb-full-elec-nolimit-coding
```

"No time limit" = 7-day safety caps (`WALL`, spec `wall_seconds` / steps
3000), not targets.

## Judge afterwards

```bash
python3 scripts/replay_jrb_wb.py \
    --base-repo examples/junoresbench_full_opt/repo \
    --truth benchmarks/JunoResBench/blind_truth_electron_full/test_full.npz \
    --snapshots runs/singlenode/<run>/snapshots \
    --out runs/singlenode/<run>/replay.csv
```

Replay materializes rows by hardlink (the multi-GB frozen side is never
copied per row). Row 0 is the pristine baseline; `replay.test.json` is
the held-out judgement.
