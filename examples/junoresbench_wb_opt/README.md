# junoresbench_wb_opt — JunoResBench white-box reconstruction task

Reconstruct per-event energy / vertex / t0 from digitized PMT waveforms of
a JUNO-like LS toy detector (single-electron population, E ~ U(1, 8) MeV),
**white-box**: the complete numpy-only forward model ships inside the task
package. Objective: `energy_res` on the labeled val split (lower is
better; ties: vertex, then timing). The judged score is the held-out test
replay — test.npz carries no truth in-container, and the harness scores it
host-side against `benchmarks/JunoResBench/blind_truth_electron/`.

Built on the blind/white-box task packages in `benchmarks/JunoResBench/`
(whitebox = blind + generator source; data/scorer byte-identical, so
blind-vs-whitebox scores compare directly on the same test events).

## Layout

- `repo/` — the world template (git repo after `setup.sh`): baseline
  solver `src/solve.py` (charge-centroid), frozen `scripts/`
  (check_verify gate + bench objective), the white-box task package under
  `benchmarks/whitebox_task_electron/`.
- `spec.json` — scientist-mode spec template (creds filled at launch).
- `pyuser/` — frozen cp39 numpy 2.0.2 user-site (numpy + dist-info +
  numpy.libs OpenBLAS). The runtime image (reused from
`examples/xsbench_opt/apptainer.sif`) has no numpy under `--containall`;
the launcher seeds this into the run's container home, so `python3`
inside the world is 3.9 + numpy 2.0 exactly.
- `setup.sh` — one-time: git-init the world template.

## Metrics contract

| key | source | meaning |
| --- | --- | --- |
| `verify: PASS` | `scripts/check_verify.sh` exit 0 | gate: well-formed, in-range prediction on val |
| `energy_res` | `scripts/bench.sh` | objective on val (lower better) |
| `vertex_res_cm`, `timing_res_ns` | `scripts/bench.sh` | tiebreakers |
| `SANITY` | `scripts/bench.sh` | anti-copy gate: floors at 0.5% / 3 cm / 0.3 ns (~1/4 of the photostatistical limit) reject val-truth copying; the held-out test replay cannot be gamed (test.npz has no truth keys) |

Baseline (charge-centroid, this population): val 15.8% / 1225 cm / 8.9 ns;
test 16.3% / 1240 cm / 8.3 ns. Design floor ~3%/√E, ~10 cm, ~1 ns.

## Run (singlenode, both arms, no time limit)

```bash
# scientist arm
bash examples/junoresbench_wb_opt/launch_singlenode.sh scientist \
    runs/singlenode/jrb-wb-elec-nolimit-scientist
# coding arm
bash examples/junoresbench_wb_opt/launch_singlenode.sh coding \
    runs/singlenode/jrb-wb-elec-nolimit-coding
```

"No time limit" = 7-day safety caps (`WALL`, spec `wall_seconds` /
`steps` 3000), not targets; the arms end when the agent concludes. The
launcher wires the four jrb-specific pieces beyond the stock override
variables: pyuser seed into the run home, `S7_BENCH_CMD` (energy_res
smoke), `TASK_FILE` (coding prompt), `WALL`.

## Judge afterwards

```bash
python3 scripts/replay_jrb_wb.py \
    --snapshots runs/singlenode/<run>/snapshots \
    --out runs/singlenode/<run>/replay.csv
# replay.csv: per-snapshot val metrics through the frozen gates
# replay.test.json: held-out test judgement (baseline vs final)
```

Row 0 is the pristine baseline; the last row before judgement is the
final state. The judge never trusts agent-reported numbers.
