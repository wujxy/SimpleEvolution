# JunoResBench Two-Tier Isolation Design

## Goal

Replace the current mixed v1/v2 layout with two benchmark tasks backed by
one hidden synthetic JUNO-like world. The task generator, generated waveform
data, and evaluator must be separately visible artifacts and have no Python
code dependency on one another.

## Scope

The two live tasks are:

1. `electron_single_site`: reconstruct an electron's visible energy and
   vertex from PMT waveforms for 1--10 MeV electrons.
2. `ibd_positron_multisite`: reconstruct an IBD-like prompt positron's
   visible energy from PMT waveforms. The positron kinetic-energy track is
   followed by exactly two 511 keV annihilation gammas, each with its own
   gamma interaction chain.

Both tasks use the same hidden detector configuration, optical parameters,
electronics parameters, and particle-transport model. They differ only in
their event populations, public calibration data, output contract, and score.

Frozen v1 artifacts remain archival and are not live task inputs.

## Physical World

The private generator is the sole authoritative implementation of the
detector world. It supports three particle paths.

### Electron

An electron is transported in charged-particle steps using the hidden
ESTAR/Bloch--Bethe-shaped stopping-power table. Each step records position,
length, midpoint kinetic energy, local `dE/dx`, and deposited energy. Local
Birks quenching is applied per step. Every step independently produces
scintillation photons and, where above threshold, Cherenkov photons.

### Gamma

A gamma is transported through sampled interaction lengths. Compton scatters
use the Klein--Nishina distribution; photoelectric absorption and the low
energy cutoff transfer the remaining energy to secondary electrons. Every
secondary electron takes the electron path above. A gamma can escape at the
detector boundary.

### Positron

A positron kinetic-energy track takes the electron-like charged-particle
path. At rest it produces exactly two back-to-back 511 keV gammas; the v2
world disables the three-gamma branch. Each annihilation gamma then takes the
gamma path above. Thus its observed signal combines the positron track and
two spatially separated gamma cascades.

For all three paths, produced photons undergo the hidden trace-optics,
PMT-detection, and electronics chain before digitized waveform output.

## Task Contracts

### `electron_single_site`

- Physics population: electrons with kinetic energy in `[1, 10] MeV`,
  uniformly distributed throughout the fiducial volume.
- Public inputs: detector geometry, labeled calibration waveforms, and a
  development split with its disclosed local truth.
- Submission output per event: finite `E_rec` in MeV and finite
  `x_rec, y_rec, z_rec` in metres.
- Energy score: fixed probe energies are fitted to
  `sigma/E = sqrt(a^2/E + b^2 + c^2/E^2)` and pass when
  `R_1MeV = sqrt(a^2+b^2+c^2) <= 0.030`.
- Vertex score: the release oracle measures the task world's vertex
  resolution with the final definition
  `sqrt(mean(||r_rec-r_true||^2))` at 1 MeV. The published threshold is
  `1.15 * oracle_resolution`, rounded upward to 0.1 cm. It is fixed only
  after the oracle validation report is accepted; no real-JUNO number is
  copied into this synthetic task.
- A valid submission must pass both the energy and vertex thresholds.

### `ibd_positron_multisite`

- Physics population: IBD-like prompt positrons, with positron kinetic
  energy in `[0, 11] MeV` plus two annihilation gammas.
- Public inputs: detector geometry, labeled calibration waveforms, and a
  development split with disclosed local scoring truth.
- Submission output per event: one finite visible-energy estimate `E_rec`
  in MeV.
- Energy score: the same fixed-probe curve and the sole target
  `R_1MeV <= 0.030`.
- Validity gate: continuous controls must be finite, monotonic, locally
  plausible, and have a global energy response with slope `[0.9, 1.1]` and
  intercept within `+-0.1 MeV`. This is an estimator-validity check, not a
  second optimization score.

## Isolation Architecture

```text
benchmarks/JunoResBench/
  world_generator/                 private source; no evaluator imports
  contract/                        data-format JSON and Markdown only
  tasks/
    electron_single_site/
      dataset/                     generated data only
      evaluator/                   standalone reader, scorer, sandbox
      TASK.md
    ibd_positron_multisite/
      dataset/                     generated data only
      evaluator/                   standalone reader, scorer, sandbox
      TASK.md
```

`world_generator/` writes data that conforms to `contract/`; it never imports
from either task evaluator. Each evaluator implements its own waveform reader
and scoring code and never imports from `world_generator/`. A dataset
directory contains only generated waveform/truth artifacts and metadata; it
contains no executable generator or evaluator code. `contract/` contains no
Python package or executable shared utility.

The unavoidable relationship is a frozen file-format contract, not a shared
implementation. A top-level black-box compatibility test generates a small
fixture through the generator, executes the evaluator as a separate process,
and checks only observable files/results. It must not import either side into
the same Python process.

## Data and Privacy Boundaries

Each task dataset is split into a public task tree and evaluator-private
blind tree. Public artifacts contain geometry, waveform observations,
documented calibration labels, development truth, task text, and evaluator
code. Private artifacts contain final observations and final truth only.
The generator source, detector parameters, seeds, per-step truth, and any
oracle reconstruction are never shipped in the public task tree.

Final evaluation runs the evaluator against a private event stream. The
submission receives one event at a time in a mount-isolated process; it cannot
read the private directory or receive the bulk stream. The submission instance
may keep causal state from earlier events, but each prediction is irrevocable.

## Release Gates

Before publishing either task:

1. Check energy conservation, local low-energy quenching, and required event
   topology from private truth.
2. Verify public/private file allowlists recursively.
3. Verify deterministic regeneration from a fixed seed and hash both truth
   and sparse waveform observations.
4. Run public baseline and reviewed private reference through the standalone
   evaluator. The reference must reach the task target; the public baseline
   must not.
5. Bootstrap the fixed-probe score and reject an unstable decision boundary.
6. For the electron task, run the private oracle vertex reconstruction and
   derive/freeze its published vertex threshold before final validation.

## Non-Goals

- No Geant4 execution during evaluation.
- No hard-coded real-JUNO energy or vertex resolution target except the
  agreed 3.0% energy acceptance line.
- No shared Python utility module between generator and evaluator, even for
  sparse waveform encoding/decoding.
- No third final task for gamma calibration-source reconstruction in this
  scope; gamma sources remain public calibration material unless separately
  specified later.
