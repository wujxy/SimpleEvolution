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
```

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
