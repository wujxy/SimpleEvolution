# junoresbench_full_std_opt — JunoResBench standard-mode reconstruction, FULL readout

The STANDARD-mode sibling of `junoresbench_full_opt` (which is the
oracle variant): same world, same gates, same held-out test judgement —
but the task package ships **no generator**. `benchmarks/
blind_task_electron_full/` carries the waveforms, the labeled train/val
calibration splits and the scorer, nothing else. How the data was
produced is the agent's research problem: model the detector response,
validate on the calibration data, use the open literature. Mode
semantics and the task-sheet discipline (definitional info stays,
mechanistic info goes) are recorded in
`docs/design/JRB-standard-oracle两模式定案.md` — in short:
**standard = eval visible, generator hidden; oracle = generator ships
(ceiling calibration only, not a headline arm).**

Everything else matches the oracle example: zero-suppressed full
readout (every channel that pulsed, physics and background alike;
silent channels absent), calibration drift on the dataset clock
(t_run_s; no stationarity guaranteed — the sheet says only that much),
single-electron population E ~ U(1, 8) MeV, objective `energy_res` on
val (ties: vertex, then timing), SANITY anti-copying floors, judged
score = held-out test replay host-side against
`benchmarks/JunoResBench/blind_truth_electron_full/`.

## Isolation contract

Mounts are the boundary (`--containall` + explicit binds; smoke S9
checks the host is invisible). Two template rules make standard mode
real rather than nominal:

- this template repo contains the task package only (renamed
  `electron_full/` — the canonical host-side name `blind_task_*` never
  appears inside any container) — the `/repo:ro` bind therefore
  exposes no generator;
- the canonical generator (`benchmarks/JunoResBench/juno_res_bench/`)
  and all truth live host-side, outside every container bind.

## Data is NOT in git

Same as the oracle example: the task-package npz files are multi-GB and
stay on disk only. Reproducibility is by seed — see
`benchmarks/JunoResBench/MANIFEST.md` (electron_full section; the blind
package's data files are the sha256-anchored originals) — and assemble
this template with:

```bash
mkdir -p examples/junoresbench_full_std_opt/repo/benchmarks
cp -a benchmarks/JunoResBench/blind_task_electron_full \
    examples/junoresbench_full_std_opt/repo/benchmarks/electron_full
bash examples/junoresbench_full_std_opt/setup.sh   # re-init if repo/.git was cleaned
```

## Layout

- `repo/` — the world template (git repo after `setup.sh`; the package
  data are untracked there too): baseline solver `src/solve.py`
  (charge-centroid, memmap-chunked over `adc.npy`), frozen `scripts/`
  (check_verify gate + bench objective; PKG points at the task
  package), the task package under
  `benchmarks/electron_full/` (neutral name: mode words like
  blind/whitebox/standard never reach the agent).
- `spec.json` — scientist-mode spec template (creds filled at launch).
- The runtime image (reused from `examples/xsbench_opt/apptainer.sif`)
  and the numpy user-site asset (reused from
  `examples/junoresbench_wb_opt/pyuser/`) are shared with the other jrb
  examples.

## Metrics contract

Identical to `junoresbench_full_opt`: `verify: PASS` gate, `energy_res`
objective on val, SANITY floors (0.5% / 3 cm / 0.3 ns), held-out test
judgement host-side. Scores are directly comparable across the standard
and oracle variants (byte-identical data files and scorer).

## Run (singlenode, both arms, no time limit)

```bash
bash examples/junoresbench_full_std_opt/launch_singlenode.sh scientist \
    runs/singlenode/jrb-full-std-elec-nolimit-scientist
bash examples/junoresbench_full_std_opt/launch_singlenode.sh coding \
    runs/singlenode/jrb-full-std-elec-nolimit-coding
```

"No time limit" = 7-day safety caps (`WALL`, spec `wall_seconds` / steps
3000), not targets.

## Judge afterwards

```bash
python3 scripts/replay_jrb_wb.py \
    --base-repo examples/junoresbench_full_std_opt/repo \
    --truth benchmarks/JunoResBench/blind_truth_electron_full/test_full.npz \
    --snapshots runs/singlenode/<run>/snapshots \
    --out runs/singlenode/<run>/replay.csv
```

Row 0 is the pristine baseline; `replay.test.json` is the held-out
judgement. The oracle runs (`jrb-full-elec-nolimit-*`) provide the
ceiling reference for the standard/oracle gap.
