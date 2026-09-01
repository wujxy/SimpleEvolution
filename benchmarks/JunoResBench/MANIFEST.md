# JunoResBench test data manifest

## Active tasks — isolated two-tier packages

`tasks/electron_single_site/` and `tasks/ibd_positron_multisite/` are the
active benchmark definitions. Their private authoritative world is in
`world_generator/`; generated waveform data are data-only public/private
trees; each task evaluator is standalone and does not import the generator.
The cross-boundary format is frozen in `contract/`. Generate release datasets
only through `world_generator/condor/` on the designated HTCondor cluster,
then retain its private release JSON next to the private truth tree.

## Frozen v1 evidence (archival — do not regenerate or rescore)

Small **intermediate-check** datasets only (not benchmark datasets). Both are
bit-exact reproducible with the pinned seeds; SHA256 for tamper detection.

| file | sha256 | events | E range (MeV) | waveforms | seed |
|---|---|---|---|---|---|
| data/jrb_test_small.npz | 090ae0d651fc86117cdea10cef652bc0b41e759833a2edb97a5dfe38faf477fd | 100 (mixed) | 1–8 | 256 ch/event, uint16 | 20261101 |
| data/jrb_scan2k.npz | 5ae3a9c574cc365f2788898b9271c179b87644a19288f1363db1c9767daaa143 | 2000 (mixed) | 1–8 | none (truth-only) | 20261102 |

## Historical packages

`data/jrb_bench_v1.npz` + the original `blind_task/` package (seed 20260910,
electron-only, t0-referenced fixed window — pre-trigger architecture) live in
git history (commit f9015a8). Same-day v4 re-issues with small date-style
seeds (20261111 electron / 20261110 mixed) were replaced by the big-seed
packages below once the white-box variant (which ships the generator source)
made seed brute-forcing a relevant attack surface.

## Benchmark packages (v1 trigger architecture, per-photon trace optics — frozen)

Global trigger on the PE rate (100-ns causal trailing window, 200-pe
threshold, dark included), readout window = [t_trig − 300, +700) ns
(1000 ns total, referenced to the window start),
per-event t0 ~ U(0, 1000) ns. Split 240/120/240; `make_benchmark.py --name
<pkg>` writes `data/jrb_<pkg>.npz` + `blind_task_<pkg>/` +
`blind_truth_<pkg>/`; `make_whitebox.py --name <pkg>` adds
`whitebox_task_<pkg>/`. Blind meta strips the generation seed; blind_truth
keeps it. **Seeds are 60-bit random values** (seed search computationally
dead — required by the white-box packages that ship the generator).

### electron (seed 588010011806800290) — the base task: waveforms → E, vertex, t0

| file | sha256 | contents |
|---|---|---|
| data/jrb_electron.npz | 925b3afd09172b7773ff14e55bdcb0a9576772d53f08e16c6aeecdcf0096ecd0 | 600 events (electron), full truth + 192 ch/event |
| blind_task_electron/train.npz | 8f08c566874fb02411658db53c38f8bc83bb98a087bc9f42d694faa6f72684be | 240 events, truth visible |
| blind_task_electron/val.npz | 72e949f0f5b29e8f09330fb80ee8be265c7f90cd715108ffcccc369a71141dfb | 120 events, truth visible |
| blind_task_electron/test.npz | 57eea66d37a9dab30f322fc4201fe64b9cf9199e009bee63755a9c01c2b8dd5f | 240 events, adc only (meta seed stripped) |
| blind_truth_electron/test_full.npz | a9de05c10e2cf5d9b64885e871e111ab7ffd792f3199645bac337a5fc082db8b | PRIVATE: test truth |

### mixed (seed 258797109207854889) — e⁻/γ/e⁺ equal thirds, type-conditional calibration

| file | sha256 | contents |
|---|---|---|
| data/jrb_mixed.npz | efdfd0d7349619c74b192e366a7f91f055cc5d3835fde291425277d59b14a584 | 600 events (realized 197/209/192 e⁻/γ/e⁺ full-set), full truth + 192 ch/event |
| blind_task_mixed/train.npz | 6c89af12221a71dcadbc52663be6191278b027244e03a09829bcb418198a66e5 | 240 events, truth + particle labels visible |
| blind_task_mixed/val.npz | 31a5666574f668951b26f3b6fdbb582168924098d0a208d3317cd8fe88845995 | 120 events, truth + particle labels visible |
| blind_task_mixed/test.npz | fbcf2849db5c5eddbf92f3e5fa64bcb8bec3f7f3f9769f391687372898b2ce9d | 240 events (77/90/73), adc only (meta seed stripped) |
| blind_truth_mixed/test_full.npz | 18758db43414801c40e886c6ec9ab7fd116b2109687b83f48aa6f37de7f2da24 | PRIVATE: test truth |


### electron_full (seed 1128258127999576769) — zero-suppressed FULL readout + calibration drift

`--full-readout --drift --out-format dir --interleave --train-events 600
--val-events 120 --max-wf-per-event 0 --optics-mode trace`, 960 events
(600/120/240 interleaved through a continuous run, mean 10 s spacing).
Rows = hit ∪ in-window-dark channels; silent channels omitted. Drift:
per-PMT gain/PDE OU (2%/1%, tau 45 min) + per-event per-PMT gain jitter
0.2% + global PDE slow mode (1%, tau 40 min) + global dark-rate log-OU
(15%, tau 30 min); NO per-event common mode (degenerate with energy).
Splits are dirs {meta.json, data.npz, adc.npy(memmap)}; shipped meta =
readout contract only (no seed, no detector_config); test strips
pmt_offsets (hit identity from waveforms) and ships t_run_s. train 600 ev / 2,969,516 rows (2,784,065 hit + 185,451 dark-only); val 120 ev / 625,403 rows (589,106 + 36,297); test 240 ev / 1,157,857 rows.

| file | sha256 |
|---|---|
| blind_task_electron_full/train/data.npz | f8a9f4dae220381d97246a0a93a84d63e8043ffa4f27549020772f6cd850d179 |
| blind_task_electron_full/train/adc.npy | 29ffcb91e845a2102cab31172037a61ea7414096a247c5bcc86bb4dbfe8fe54c |
| blind_task_electron_full/val/data.npz | c322933134c4f0d5907f5ac820ef31342009ea7aef03e927623558f6a4e85a13 |
| blind_task_electron_full/val/adc.npy | a29590d4fa1c9613c7e162a2e16366dcef4e5cc72eadd747782d482d3f48cd53 |
| blind_task_electron_full/test/data.npz | 5814ed755478946c399e9847096cac2ed89a1d618fd41db8fe62c2de7b3f4964 |
| blind_task_electron_full/test/adc.npy | 78489a048b03a1527b16d1e8679751ef4dff9168c93264b21e40900dd1eb9034 |
| blind_task_electron_full/pmt_positions.csv | 86b23690947e46974c89eae8a16b3a7bcb7649c38537e7613afb5013a0a78ab8 |
| blind_task_electron_full/pmt_datasheet.md | 975cd146424278c67d1282321bac79d370e341fe22ded51de4cc35a6ce084861 |
| blind_truth_electron_full/test_full.npz | 02587c10ec16b309616145d58aaff00ed76a6c7b2c0245294c879d2d89e2c2e0 |

`pmt_positions.csv` was added 2026-08-28 for the standard mode (see
docs/design/JRB-standard-oracle两模式定案.md): channel positions are instrument
description, not mechanism — exported once from the package generator's
`fibonacci_sphere(17612, 19.365)` layout. `pmt_datasheet.md` same day:
type-level production-test nominal specifications (typicals around the
generator's truth; per-channel spreads quoted as uniformity figures) —
the as-run values are the calibration problem, not the sheet's business.
| data/jrb_electron_full/data.npz | 84ac0159112b745ed867925abd7b11a2201edbe474dc0b746d9b1c5aeb99f11a |
### whitebox_task_electron/ — white-box variant (blind + generator source)

Byte-identical data files and scorer as blind_task_electron (train/val/test
sha256 above; evaluate.py 02559d02d196ef531c3fbeee15cd9d383ddf6f3343bc117d66dfea098c9e8ad4),
plus the complete numpy-only forward model: `juno_res_bench/` (stages 1-5,
tracks the repo source at build time) and a package-local
`generate_dataset.py` (cf8cd3989871312f49d373672ad4e3dfbcf74ecfd5826a7009cba9df22334e50)
so the agent can generate unlimited labeled data under any seed of their
choosing. TASK.md = blind sheet + white-box preamble. Build/self-check:

```bash
python3 scripts/make_whitebox.py --name electron
# checks: byte identity vs blind, no seed in any meta, standalone
# simulator smoke run from a foreign cwd
```

Regenerate everything:

```bash
cd benchmarks/JunoResBench
python3 scripts/make_benchmark.py --name electron --events 600 \
    --seed 588010011806800290 --particle-type electron
python3 scripts/make_benchmark.py --name mixed --events 600 \
    --seed 258797109207854889 --particle-type mixed --mix 1,1,1
python3 scripts/make_whitebox.py --name electron
# reference baseline scores
python3 baselines/charge_centroid.py --data blind_task_<pkg>/test.npz \
    --train blind_task_<pkg>/train.npz --out pred.npz
python3 scripts/evaluate.py --data blind_truth_<pkg>/test_full.npz \
    --pred pred.npz --out blind_truth_<pkg>/baseline_test_score.json
# intermediate-check sets
python3 scripts/generate_dataset.py --events 100 --emin 1 --emax 8 \
    --seed 20261101 --max-wf-per-event 256 --particle-type mixed \
    --direction isotropic --out data/jrb_test_small.npz
python3 scripts/generate_dataset.py --events 2000 --emin 1 --emax 8 \
    --seed 20261102 --truth-only --skip-per-pe --particle-type mixed \
    --direction isotropic --out data/jrb_scan2k.npz
sha256sum data/*.npz blind_task_*/*.npz blind_truth_*/test_full.npz
```

Notes:

- Stage-5 physics: Birks/nl/Cherenkov/scatter/CE/PDE/time offsets/afterpulses
  + v1 particle chain (gamma KN Compton + escape, positron annihilation +
  o-Ps) + **trigger architecture** (global PE-rate trigger defines the
  window; dark noise on all channels participates in triggering and rides
  the waveforms but not the physics truth).
- Scoring (evaluate.py): E_ref = e_true + 1.022 MeV for positrons (JUNO
  convention), resolution = (q84-q16)/2 quantile width, vertex = 68%
  quantile, **timing = (q84-q16)/2 of t0_rec − t0_ref with t0_ref =
  evt_t0 − (evt_t_trigger − 300 ns)** — the event time is only observable
  window-relative (the trigger follows the event; correcting its latency
  requires the vertex).
- Baselines to beat (charge_centroid on the blind test; identical test
  events in the white-box package, so the same numbers apply):
  - electron: energy 0.163 / vertex 68% 12.4 m / timing 8.3 ns (bias +4.0%)
  - mixed: energy 0.159 / vertex 12.2 m / timing 7.9 ns (bias +5.9%; per
    type e⁻ 0.151 / γ 0.135 / e⁺ 0.152 — type-conditional calibration task)
- The golden-digest lock in tests/test_stage0.py is numpy-build dependent:
  python 3.11.10 / numpy 1.26.4 (this host). Regenerate the digests together
  with the full e- anchor suite if the build changes.

### electron_static (seed 276263006192544257) — zero-suppressed FULL readout, STATIC detector

`--full-readout --out-format dir --train-events 600 --val-events 120
--max-wf-per-event 0 --optics-mode trace`, 960 events (600/120/240,
contiguous). Rows = hit ∪ in-window-dark channels; silent channels
omitted. NO drift, no run clock, no t_run_s — the static-detector
convention (JUNO MC): per-PMT calibration (gain 15% / PDE 8% / time
offset 1 ns Gaussian) drawn once and fixed; difficulty is detection
physics only (statistics-starved per-PMT calibration from 600 train
events, physics-vs-dark pulse separation, trigger-latency t0). Splits
are dirs {meta.json, data.npz, adc.npy(memmap)}; shipped meta = readout
contract only (no seed, no detector_config, no drift flag); test strips
pmt_offsets (hit identity from waveforms) and ships adc_pmt_ids +
wf_offsets only. train 600 ev / 2,963,025 rows (2,778,661 hit + 184,364
dark-only); val 120 ev / 604,353 rows (567,963 + 36,390); test 240 ev /
1,182,945 rows. Baseline (world solve.py): val 13.1% / 608 cm / 4.9 ns,
test 12.7% / 600 cm / 6.2 ns. Supersedes electron_full's drift
machinery, which was removed from the generator (environmental physics
is a calibration-operations problem, not detection physics; four of its
five OU modes were statistically invisible anyway).

```
269b1a9d274f983f2344ac101c446e81655f77b213c17f23766066b2ab1bfba2  blind_task_electron_static/train/data.npz
c4a786815986eaa38d610579f62da87af6fbbc407b1b37015f951169fed91fa1  blind_task_electron_static/train/meta.json
8cdb7ea86b8c721116f7d543aa98112379e7f4d2cbc7a5ab530be84b5718be21  blind_task_electron_static/val/data.npz
c4a786815986eaa38d610579f62da87af6fbbc407b1b37015f951169fed91fa1  blind_task_electron_static/val/meta.json
97fc5cf5be631b980654957421c848d501388c96afa6612d75943dcaf2d9a205  blind_task_electron_static/test/data.npz
c4a786815986eaa38d610579f62da87af6fbbc407b1b37015f951169fed91fa1  blind_task_electron_static/test/meta.json
21d252c33dbaeaf50ee89751abff95b0897c8f99cc4d16e7ca2e6808ab45a5b9  blind_truth_electron_static/test_full.npz
```
