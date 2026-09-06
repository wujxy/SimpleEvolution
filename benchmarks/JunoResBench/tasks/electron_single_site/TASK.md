# JunoResBench — Single-Electron Energy and Vertex Reconstruction

Reconstruct 1–10 MeV single-electron events from PMT waveforms in a
JUNO-like liquid-scintillator detector, the way an experiment analyst
would calibrate the detector and then reconstruct physics events.

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
fit gives `R_1MeV` (target 3.0%), and the 1-MeV vertex RMS is reported
against `vertex_rms_reference_m`.

## Environment

The evaluator sends hidden events one at a time into a mount-isolated
worker with an 8 GiB address-space limit; per-event memory must stay
bounded, and caching the full event stream will not fit. The private data
directory and the generator are not supplied.
