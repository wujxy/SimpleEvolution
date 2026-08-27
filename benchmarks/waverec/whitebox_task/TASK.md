# White-box waveform reconstruction task

## White-box setting

This is the white-box variant: the COMPLETE forward model that produced the
data ships with the package —

  - `wavegen/`              the waveform generator (numpy only): pulse
                            shape, SPE charge spectrum, pile-up, baseline,
                            noise and digitization
  - `generate_dataset.py`   command-line entry to generate labeled
                            waveforms under any seed of your choosing

You may read, run and modify any of it — e.g. generate unlimited labeled
training data, build the exact matched-filter/deconvolution kernel from
the known pulse shape, or fit per-event pulse trains directly.

The test set was produced by exactly this code with an unknown seed
(absent from this package; brute-force seed search is not the task).

`data/`, the scorer and the metrics are byte-identical to the blind
variant — scores are directly comparable across the two.

## The data

`data/` contains digitized waveforms from a single photodetector channel,
produced by a realistic detector simulation:

- Each event is one readout window: **1000 samples, 1 sample = 1 ns**,
  14-bit ADC (values 0–16383, in ADC counts).
- Photons hit the photocathode and produce **photoelectrons (PEs)**. Every PE
  generates an identical fast pulse (order 10 ns) whose amplitude is
  proportional to the PE's charge (in units of pe); pulses **superpose
  linearly**. PE arrival times within a window are random, so pulses frequently
  overlap (**pile-up**).
- The summed signal sits on a positive DC baseline with **negative-going
  pulses**, plus small white electronics noise.

| file | events | truth |
|---|---|---|
| `data/waverec_train.npz` | 400 | yes |
| `data/waverec_val.npz` | 100 | yes |
| `data/waverec_test.npz` | 300 | **no** — this is what you are scored on |

Each npz uses a ragged layout:

| key | shape | meaning |
|---|---|---|
| `adc` | (N, 1000) int32 | waveforms, ADC counts |
| `n_pe` | (N,) int32 | true PE count per event *(train/val only)* |
| `t_offsets` | (N+1,) int64 | offsets into the flat truth arrays *(train/val only)* |
| `t_hits` | (Σpe,) float64 | true PE times, ns from window start *(train/val only)* |
| `amplitudes` | (Σpe,) float64 | true PE charges, pe *(train/val only)* |

## Your goal

Build a solver that, for each **test** waveform, predicts how many PEs it
contains and their times and charges.

**Any method is allowed**: threshold/integration, matched filtering, template
fitting, deconvolution, machine learning trained on the provided truth,
techniques from the literature, or anything else you can justify. The detector
response — pulse shape, charge spectrum, noise level, baseline — is fully
documented by the included `wavegen/` source: reading, running and modifying
the generator is part of the white-box toolkit.

## Deliverable

A single file `prediction.npz` in the same ragged layout:

```python
import numpy as np
d = np.load("data/waverec_test.npz")
N = d["adc"].shape[0]
np.savez("prediction.npz",
         n_pred=np.zeros(N, dtype=np.int32),      # predicted PE count per event
         t_offsets=np.zeros(N + 1, dtype=np.int64),
         t_pred=np.zeros(0),                      # predicted times  [ns]
         a_pred=np.zeros(0))                      # predicted charges [pe]
```

## Scoring

```bash
python3 evaluate.py --data data/waverec_test.npz --pred prediction.npz
```

Predicted pulses are matched one-to-one to true PEs (greedy, nearest first)
within a **20 ns** tolerance. Reported metrics: efficiency (matched / true),
purity (matched / predicted), time RMSE over matched pairs, and charge
relative bias / RMSE.

Primary ranking: **efficiency subject to purity ≥ 0.98** (higher is better),
ties broken by lower time RMSE, then lower charge relative RMSE.

## Rules

- The train/val truth may be used freely — calibration, validation, training
  ML models, anything.
- Do not try to guess random seeds or brute-force a data generator; there is
  no recoverable shortcut behind the test set.
- Everything else goes.
