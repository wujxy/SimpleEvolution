# waverec — generic waveform reconstruction benchmark

A **detector-agnostic** benchmark: given digitized PMT-like waveforms,
reconstruct the underlying pulse train (hit times + charges). The task is
"truth → waveform" generation plus frozen datasets with ground truth, a
simple baseline reconstructor, and a scorer. What reconstructs the waveform
(the agent, an ML model, a fit algorithm) is out of scope here.

## Forward model

```
adc(t) = Q14bit( baseline_V + polarity * Σ_j a_j · spe(t − t_j) + noise )

spe(t)  = exp( −ln((t−shift)/width)² / (2μ²) )        # log-normal single-PE pulse
a_j     ~ (1−p_tail)·N(gain, σ_gain) + p_tail·(Exp(decay)+cutoff)   # SPE charge spectrum, pe
noise   ~ N(0, σ_noise)
```

| Parameter | Default | Provenance |
|---|---|---|
| sample interval / window | 1 ns × 1000 samples | JUNO ElecSimV3 `ElecSimSvc` (pre 300 + post 700 ns, 1 GSa/s) |
| ADC | 14 bit, 1 V full scale, baseline 0.292 | ElecSimV3 `TriggerHandlerLpmtHelper` new-FADC params |
| pulse shape | log-normal, width 13 ns, μ 0.43, shift 6−13/1.5 ns | `pmtPulse()` in `TriggerHandlerLpmtHelper.cc` |
| SPE spectrum | gain 1 pe, σ 30%, 10% exp tail (decay 2.2, cutoff 0.1) | `PulseGen_NNVT::calculateAmplitude` |
| 1-pe peak amplitude | 7 mV | measured JUNO waveform templates (run 9487) |
| noise | 0.35 mV RMS white | configurable |

Nothing is JUNO-specific: every constant lives in
`wavegen/config.py:WaveGenConfig` and can be overridden. The generator only
assumes a PMT-like chain (log-normal/analog shaping + FADC + white noise).

## Layout

```
wavegen/                     generator package (frozen once data is cut)
  config.py                  WaveGenConfig / SPEParams dataclasses
  generator.py               WaveformGenerator: truth -> digitized waveform
scripts/
  generate_dataset.py        dataset production (npz: waveforms + truth)
  check_generator.py         self-checks: determinism, linearity, digitizer
  evaluate.py                score predictions vs truth
baselines/
  threshold_integrator.py    COTI-like reference reconstructor
data/                        frozen datasets (see MANIFEST)
runs/                        baseline outputs + scores (not truth)
figures/                     explanatory figures (see below)
blind_task/                  self-contained task package given to agents (see below)
blind_truth/                 PRIVATE: held-out test truth + meta + reference scores
```

## Figures

`python3 scripts/make_figures.py` regenerates three views of the forward
model (drawn from `wavegen` itself, so they always match the generator):

- `fig1_spe_and_pulse.png` — the single photoelectron: sampled SPE charge
  spectrum vs its analytic model (Gaussian core + exponential tail), and the
  fixed log-normal pulse shape (7 mV peak, ~12 ns FWHM, vs the 0.35 mV noise
  floor).
- `fig2_forward_model.png` — one event, step by step: true hit times →
  individual pulse copies → their sum (pile-up appears here) → the digitized,
  polarity-flipped, noisy waveform the solver actually sees.
- `fig3_pileup.png` — three real nominal-dataset events (well separated /
  moderate / severe pile-up) with true hit times and the baseline
  reconstructor's output overlaid; red markers are true PEs the threshold
  integrator loses to pile-up.

## Datasets (v1)

Ragged layout, one `.npz` per difficulty:

| key | shape | meaning |
|---|---|---|
| `adc` | (N, 1000) int32 | waveforms |
| `n_pe` | (N,) int32 | true pulse count |
| `t_offsets` | (N+1,) int64 | offsets into `t_hits`/`amplitudes` |
| `t_hits` | (Σpe,) float64 | true hit times, ns from window start |
| `amplitudes` | (Σpe,) float64 | true charges, pe |
| `meta` | json str | full WaveGenConfig + seed |

| file | events | mean pe | noise | seed |
|---|---|---|---|---|
| `data/waverec_v1_snr_nominal.npz` | 300 | 10 | 0.35 mV | 20260824 |
| `data/waverec_v1_snr_low.npz` | 300 | 10 | 1.0 mV | 20260825 |
| `data/waverec_v1_sparse.npz` | 300 | 2 | 0.35 mV | 20260826 |

Pulses are uniform over the window; with ~150 ns pulse length and mean
10 pe / 1000 ns, the nominal sample has substantial **pile-up** — that, not
noise, is the dominant reconstruction difficulty (see baseline efficiency).

## Usage

Regenerate data (bit-exact, seeded):

```bash
python3 scripts/generate_dataset.py --out data/foo.npz --events 300 \
    --mean-pe 10 --seed 20260824
python3 scripts/check_generator.py        # self-checks before trusting data
```

Reconstruct + score (the contract any future solver follows):

```bash
python3 baselines/threshold_integrator.py \
    --data data/waverec_v1_snr_nominal.npz --out runs/pred.npz
python3 scripts/evaluate.py \
    --data data/waverec_v1_snr_nominal.npz --pred runs/pred.npz
```

A solver reads the dataset npz and writes a prediction npz with keys
`n_pred (N,)`, `t_offsets (N+1,)`, `t_pred`, `a_pred` (ns, pe). Matching is
greedy nearest within `--tol-ns` (default 20 ns), one-to-one.

## Baseline results (threshold_integrator, k=5σ)

| dataset | efficiency | purity | time RMSE (ns) | charge bias | charge RMSE |
|---|---|---|---|---|---|
| snr_nominal | 0.661 | 0.999 | 1.27 | +0.34 | 0.96 |
| snr_low | 0.695 | 0.999 | 1.77 | −0.03 | 0.65 |
| sparse | 0.899 | 0.998 | 1.16 | −0.01 | 0.37 |

Interpretation: purity is near-perfect (5σ threshold), but efficiency is
capped ~0.66–0.70 at mean 10 pe because overlapping pulses merge into one
window and are counted once (visible as the +0.34 charge bias on the
nominal set: matched "pulses" are often 2-pe merges). A better solver
separates pile-up (deconvolution / template fit / ML) — that is the headroom
this benchmark measures.

## Blind task package (`blind_task/`)

The self-contained package a solver/agent under test receives — and nothing
else. Design principle: give the physical meaning of the data and the
reconstruction goal, then leave the method fully open (thresholding,
deconvolution, template fits, ML, literature — anything). The forward model,
its parameters, and the baseline are deliberately **withheld**; characterizing
the detector response from the data is part of the task.

```
blind_task/
  TASK.md                    one page: physics, goal, format, scoring, rules
  data/waverec_train.npz     400 events, truth visible (seeds 20260901)
  data/waverec_val.npz       100 events, truth visible (seed 20260902)
  data/waverec_test.npz      300 events, adc only — meta & truth stripped (seed 20260903)
  evaluate.py                standalone scorer (numpy only)
```

Note the npz `meta` key (which embeds the full generator config) is stripped
from all three files; test truth lives only in `../blind_truth/`, which is
**not** part of the package. Score submissions against the private copy:

```bash
python3 blind_task/evaluate.py \
    --data blind_truth/waverec_test_full.npz --pred prediction.npz
```

Ranking: efficiency subject to purity ≥ 0.98, then time RMSE, then charge
relative RMSE. Reference to beat (threshold integrator on the blind test
set): efficiency 0.671, purity 0.999, time RMSE 1.25 ns, charge bias +0.31
(`blind_truth/baseline_test_score.json`).

## What is simplified relative to JUNO ElecSimV3

The generator reproduces the **single-channel forward physics** — SPE spectrum →
fixed pulse shape → linear superposition → FADC digitization — with defaults
taken from ElecSimV3. JUNOSW's electronics simulation, however, is a
trigger-driven full-detector system; the following are deliberately absent:

| JUNOSW ElecSimV3 | wavegen |
|---|---|
| input = optical-sim `SimPMTHit` (nPE, hit time, incidence angle θ, TOF) | truth given directly as (t_j, a_j) |
| per-PE charge: measured per-PMT SPE histogram / MCP gamma model / parameterized model with θ-dependent exponential-tail fraction (`PulseGen_NNVT::calculateAmplitude`) | one parameterized spectrum, fixed tail fraction |
| per-PMT gain / TTS / time-offset calibration from DB (`get_gain(pmtid)` …) | single channel, per-event gain spread only |
| dark noise: per-PMT DCR from DB, Poisson-sampled (`generateDarkPulse`) | `dark_rate_hz` hook, default 0 |
| after-pulses per PE (MCP AP model, `generateAfterPulses`) | absent |
| measured waveform-template library per run/PMT (120 samples each) | analytic log-normal fitted to those templates |
| **trigger chain**: sliding over-threshold on FADC samples (4 consecutive) → per-PMT TQ → global trigger defines the readout window; event mixing folds several physics events into one window (`TriggerSimAlg`, `EvtMixingSvc`) | none: fixed 1000 ns window, unconditional readout; pile-up controlled directly by mean_pe |
| dual-gain FADC (auto high/low switch), overvoltage clamp, sliding FPGA baseline, overshoot tail | single range, clip, fixed baseline |
| white or frequency-dependent per-PMT noise (FFT) | white Gaussian |
| SPMT CATIROC / TT digitization boards | one generic chain |

Consequence: the statistics of a single waveform are JUNO-like (same
parameter provenance), but the *organization* of the readout (trigger-defined
events) and most detector imperfections are not simulated. The dropped effects
are also the natural difficulty tiers if the benchmark is hardened later:
TTS + dark noise (config-only, `tts_sigma_ns` / `dark_rate_hz`) → after-pulses
→ correlated noise / dual gain (needs synthesizer work).

## Provenance notes

- Generator defaults mirror JUNO ElecSimV3
  (`/cvmfs/juno.ihep.ac.cn/el9_amd64_gcc15/Release/J26.4.1/junosw/Simulation/ElecSimV3/`)
  so the data is representative of a real LS-PMT readout chain; see the table
  above for the file-level mapping.
- The SPE spectrum here is **synthetic** (chosen parameter values, not the
  per-PMT measured histograms in `PMTParam_CD_LPMT_SPE.root`) — the benchmark
  is deliberately generic, not JUNO-channel-faithful.
- If a SimpleEvolution `examples/*_opt` package is wanted later, the frozen
  datasets here become `repo/assets/` and `scripts/evaluate.py` becomes the
  eval command; nothing else needs to change.
