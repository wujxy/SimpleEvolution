# JunoResBench v2 — Positron Visible-Energy Reconstruction

## Background

A spherical liquid-scintillator detector of JUNO type is instrumented with
photomultiplier tubes on a surrounding sphere. Scintillation light produced
by charged-particle energy deposition propagates through the scintillator
and is detected as photoelectrons, which the PMTs and electronics turn into
digitized waveforms. Calibrating and reconstructing the energy of an event
from these waveforms is the core analysis problem of such a detector.

Every physics event in this benchmark is one inverse-beta-decay prompt
positron: the positron deposits its kinetic energy `Ek` in the scintillator
and annihilates into two 511 keV gammas, giving a total prompt-energy budget
of `Ek + 1.022 MeV` before any particles escape. Events occur throughout the fiducial volume. Their true
energies and true vertices are hidden and are never published.

The detector is synthetic: it follows the physical mechanisms that matter
for this measurement, but its numerical parameters are not those of the real
JUNO detector. The authoritative generator, its parameters, and any
per-step truth are absent from this package. You are expected to inspect
the supplied data, form hypotheses about the detector response, and build
your own reconstruction and calibration. No method is prescribed and no
approach is excluded by anything below except the output contract.

## Supplied data

All paths are relative to this `task_v2/` directory.

- `detector_geometry.npz` — array `pmt_positions_m`, shape `[n_pmt, 3]`,
  metres, centre of the detector at the origin. Channel identifiers in the
  waveform data index rows of this array.
- `calibration/` — sparse-waveform events from documented calibration
  sources. `labels.npz` inside the directory holds one row per event with
  the documented `source_energy_mev` and `deployment_position_m` `[x, y, z]`.
- `dev/` — physics events for local iteration, with `truth.npz` holding the
  scoring keys `evt_sample_role` (`0` = fixed-energy probe, `1` =
  continuous-energy control), `evt_e_true` (positron kinetic energy, MeV),
  and `evt_e_vis` (true visible energy, MeV), one row per event in stream
  order.
- `evaluate.py`, `submission_api.py`, `juno_res_bench/` — the local
  evaluator, the submission contract, and the exact scoring modules it uses.
- `baseline.py` — the supplied charge-only reference submission.
- `metadata.json` in each split directory documents the shared waveform
  configuration: `baseline` (ADC), `n_samples`, `sample_interval_ns`,
  `threshold_adc`, `pre_samples`, `post_samples`, `window_ns`.

Sparse waveform format: for each event, only waveform regions of interest
are stored. A segment is one merged region on one channel: its PMT id, its
start sample, and its samples stored as `int16` residuals `adc - baseline`
(ADC counts; pulses go downward). Segments of one event are contiguous in
stream order. Use `juno_res_bench.sparse_waveforms.SparseSplit` (shipped
above) to stream events; `SparseEvent.to_dense()` reconstructs any channel
with the baseline filled outside the regions.

## Task

Write a submission module exposing a class named `Submission` that
implements `submission_api.Submission`:

- `prepare(calibration_path, geometry_path)` — called exactly once before
  any prediction. Calibrate from the public calibration split and geometry
  in any way you see fit.
- `predict(event)` — called exactly once per streamed event with a single
  `SparseEvent`. Return one finite reconstructed visible energy `E_rec` in
  MeV.

The reconstruction target is the event's visible energy (the energy scale
probed by the calibration sources). Vertex reconstruction is neither
required nor scored; use it internally if it helps.

Every event must receive exactly one prediction. Event rejection, batch
access to the evaluation stream, and any dependence on observing the
dataset as a whole before reconstructing an event are not part of the task.

## Score

For every valid submission the evaluator reports the resolution at each
fixed probe energy, the fitted resolution coefficients `a`, `b`, `c`, and
one scalar `R_1MeV`. The sole success condition is

```
R_1MeV <= 3.0 %
```

Procedure (exact implementation in `juno_res_bench/resolution.py`):

1. The evaluation population interleaves fixed-energy probes at ten
   positron kinetic energies with continuous-energy controls. At each probe
   energy, the reconstructed-energy peak is fitted with a deterministic
   Gaussian procedure: three iterations of mean/`sigma` (ddof=1) estimation
   with re-selection inside a ±2.5 `sigma` window; a peak with fewer than
   100 finite events is not fittable.
2. The per-point resolutions are fitted with
   `sigma/E_vis = sqrt((a/sqrt(E_vis))^2 + b^2 + (c/E_vis)^2)`.
3. `R_1MeV = sqrt(a^2 + b^2 + c^2)` — the full fitted curve evaluated at
   1 MeV, not a single 1 MeV sample and not the coefficient `a`.

A submission is invalid, and receives no score, if any output is missing or
non-finite, or if its response on the continuous controls is not a usable
calibrated energy response. The deterministic validity check: 64 equal-width
truth bins each with at least 100 events; at least 60 of 63 adjacent
reconstructed bin means must increase; fewer than 5 adjacent-bin slopes
`dE_rec/dE_vis_truth` may fall outside `[0.5, 1.5]`; and a least-squares
line through the 64 bin means must have slope in `[0.9, 1.1]` and intercept
within `[-0.1, 0.1] MeV`. These checks define
whether the output is an energy estimator; they are not optimization
targets.

## Evaluation protocol

Final evaluation runs `evaluate.py` against a hidden stream that this
package does not contain. `prepare` is called once, then `predict` once per
hidden event; predictions are collected by the evaluator and scored after
the stream ends. The same `Submission` instance persists, so causal state
from earlier events is allowed, but each returned prediction is irrevocable
and future/batch access is unavailable. The submission runs in a
mount-isolated worker containing
only its module and the public `task_v2/` tree; the private data directory is
not mounted. Events cross the boundary one at a time over the evaluator's
binary protocol. Linux `bubblewrap` (`bwrap`) is therefore required by the
reference evaluator.

Runtime and memory limits: the supplied charge baseline completes a
development-scale evaluation within about two minutes on the reference
machine. A submission whose development-scale evaluation exceeds one hour
of wall time or 8 GB of peak memory is rejected. Offline precomputation
from public data (caching features derived from `calibration/` and `dev/`)
is unconstrained.
