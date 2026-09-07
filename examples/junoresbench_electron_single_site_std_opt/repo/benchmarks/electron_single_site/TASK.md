# JunoResBench — Single-Electron Energy and Vertex Reconstruction

Reconstruct 1–10 MeV single-electron events from PMT waveforms in a
JUNO-like liquid-scintillator detector, the way an experiment analyst
would calibrate the detector and then reconstruct physics events.

## Why this task exists

JUNO determines the neutrino mass ordering from the fine vacuum-oscillation
structure of the reactor ν̄_e spectrum. At the ~53 km baselines the two
orderings shift the oscillation maxima slightly, and resolving that
difference is the reason the experiment demands an energy resolution of
σ_E/E ≈ 3%/√(E/MeV) with the energy scale known to better than 1%. The
reconstructed spectrum therefore has to be sharp, unbiased, and absolutely
calibrated at the same time: making peaks sharper while shifting their
centers contributes nothing to the oscillation fit. Vertex reconstruction
carries the same physics weight — the fiducial cut it enables suppresses
the cosmogenic background that would otherwise contaminate the reactor
sample.

This benchmark is the single-electron core of that problem. Your
reconstruction is scored exactly the way the physics consumes it.

## The goal

- Primary target: drive the fitted 1-MeV energy resolution R_1MeV toward
  3.0%.
- Secondary targets, equally binding under the same physics budget: keep
  the energy response unbiased across 1–10 MeV, keep the reconstructed
  vertex free of radial bias, and keep the 1-MeV vertex resolution small.

Gates enforce the minimum each of these must satisfy, frozen in
`public/evaluation_config.json`; targets are what ranking follows. Failing
any gate marks the run invalid no matter how good the primary number
looks.

## Detector and readout

The scintillator volume is viewed by the PMTs listed in
`detector_geometry.npz` (positions and types of Hamamatsu, NNVT, and
high-QE NNVT tubes). The readout digitizes **every** PMT for 1000 samples
at 1 GHz (a 1 µs window, 14-bit ADC, stored as int16 residuals from a
fixed baseline), and every waveform is stored in full — no channels or
samples are suppressed. Events trigger globally and the window covers 300
samples before and 700 samples after the trigger time. Waveforms contain
dark noise from PMT dark rates on top of any signal; its per-channel
level is measurable from the data.

`Submission.prepare(calibration_path, geometry_path)` runs once; then
`Submission.predict(event)` is called for each hidden event and must
return `(E_rec, x_rec, y_rec, z_rec)` as four finite floats in MeV and
metres.

## Data

Three datasets accompany the task:

- `calibration/` — labeled calibration events from standard deployed
  sources: every event was produced at one of 13 known positions (the
  detector center and ±8 m and ±14 m along each axis) with one of five
  known energies (0.511, 1.022, 2.223, 4.44, 8.0 MeV). `labels.npz`
  provides `source_energy_mev` and `deployment_position_m` for every
  event. These are the only labeled waveforms of this detector; use them
  to build the response model — charge-to-energy scale, its position
  dependence, per-PMT differences, and the noise floor.
- `dev/` — physics events previously collected with this detector. They
  carry no truth; study them as you would real data.
- `final/` — the hidden evaluation events, kept private and sent to the
  evaluator one at a time.

The evaluation events are single electrons with kinetic energy in
1–10 MeV, occurring uniformly within the 16 m fiducial sphere. The
response model must interpolate across the whole fiducial volume, not
only at the calibration positions. Scoring probes monoenergetic samples
at the 1–10 MeV integer energies and also scores a continuous-energy
control sample drawn from the same population.

## Scoring

Gates decide validity; failing any gate marks the run invalid. Every gate
threshold is frozen in `public/evaluation_config.json`.

- energy bias: |bias(1 MeV)| ≤ 1.5% and max_E |bias(E)| ≤ 2.0%
- continuous response: monotone, with a globally consistent energy scale
  and offset
- vertex bias: max_E |radial bias(E)| ≤ 0.20 m
- vertex stability: max_{E≥3 MeV} vertex_RMS(E) ≤ 1.20 × vertex_RMS(1 MeV)
- energy resolution floor: R_1MeV ≤ `energy_resolution_gate`
- vertex resolution floor: 1-MeV vertex RMS ≤ `vertex_resolution_gate_m`

Optimization targets, reported and ranked: the JUNO-style resolution-curve
fit gives `R_1MeV` (primary target 3.0%), and the 1-MeV vertex RMS is
reported against `vertex_rms_reference_m`.

## Environment

The evaluator sends hidden events one at a time into a mount-isolated
worker; per-event memory must stay bounded (an 8 GiB address-space limit
makes caching the full event stream impossible). The whole evaluation,
every event, must finish within one hour. The worker sees only this
task's runtime: the system `python3` with `numpy`/`scipy`, your
`submission.py`, and the public `data/` directory — no network, no other
files. The private data directory and the generator are not supplied.
Nothing else about your submission is restricted.
