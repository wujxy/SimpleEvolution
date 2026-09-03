# JunoResBench JUNO-world redesign

## Decision

The current electron release is not publishable as a research benchmark. It
may remain an archival diagnostic fixture, but it must not be described as a
validated long-horizon reconstruction challenge.

The replacement keeps JunoResBench method-agnostic. A participant receives
waveform data and reconstructs `E, x, y, z`; the sole success target is an
energy resolution of 3.0% at 1 MeV. The task neither names nor rewards a
particular reconstruction family. Difficulty must arise from the observable
physics, never from prohibiting a shortcut.

The selected implementation strategy is a JUNO resolution-budget-matched
surrogate: retain the fast vectorized particle/photon pipeline, but replace
the simplifications that currently collapse the energy inverse problem to
total charge plus radius. Full JUNO Geant4 is a validation reference, not the
production dependency.

## Evidence and root cause

The current world already includes local charged-particle steps, local Birks
quenching, scintillation and Cherenkov production, wavelength-resolved photon
tracing, absorption/re-emission, Rayleigh scattering, PMT detection, dark
noise, afterpulsing, SPE charge variation and digitized waveforms.

Despite this procedural detail, its electron energy response is approximately

```text
E_true -> deterministic E_vis(E_true)
       -> photon count
       -> scalar radial collection p(r)
       -> photoelectron count
       -> integrated waveform charge.
```

The production path uses a rotationally symmetric Fibonacci PMT sphere, a
smooth explicit radial response polynomial and one static detector state for
calibration, development and final data. At fixed energy and radius, most
remaining fluctuations average over roughly 1,500 photoelectrons per MeV.
The measured waveform-integral/energy correlation of the failed release is
0.99898. Consequently total charge and radius are nearly sufficient
statistics, and both research agents crossed 3% without needing the detailed
waveform information.

## Alternatives considered

### Add more generic noise and trace processes

This is the smallest code change, but it is rejected. Irrecoverable noise can
move every method above 3% without creating a scientific reconstruction
problem. Additional trace operations also do not help if their event-level
effect is still a scalar collection efficiency.

### Use full JUNO Geant4 and electronics simulation

This is the highest-fidelity option, but it is not the benchmark production
path. Its runtime, software environment and distribution constraints conflict
with reproducible large releases. Small full-MC samples may later be used as
external validation.

### Build a resolution-budget-matched surrogate

This is the selected option. It adds only effects that break the current
low-rank response or make recoverable waveform information material, and it
anchors their distributions to public JUNO measurements. It preserves fast
batch production and the existing generator/data/evaluator separation.

## Upgraded physical world

### 1. Real detector geometry and PMT populations

Production must use a frozen, provenance-recorded JUNO LPMT position table,
not `PMTLayout.uniform`. Every PMT record contains position, inward normal and
technology class. The world represents the installed HPK dynode-PMT and NNVT
MCP-PMT populations rather than drawing all channels from one generic model.

Type-level parameter tables define wavelength/angle-dependent PDE, collection
efficiency, reflectance, transit-time spread, dark-rate distribution, SPE
charge response, pulse template, prepulse/late-pulse behavior and afterpulse
behavior. Per-PMT deviations are sampled once from measured population
distributions and remain stable within one detector realization.

The exact private per-PMT parameters are not included in the task data.
Participants receive PMT positions, stable public identifiers and waveform
calibration observations, which are the physically appropriate means of
inferring response.

### 2. Non-separable optical response

Trace mode remains per photon. The explicit `mu_pe_ratio(r)` polynomial is
removed from the trace production path; a trace result must not subsequently
be forced back into a scalar radial response.

The minimum boundary model contains the LS volume, the acrylic vessel and the
water gap to the PMT sphere. It applies wavelength-dependent absorption,
refraction/reflection at material interfaces and propagation time. A frozen
coarse shadow/acceptance map represents the dominant support-structure and
coverage asymmetries. PMT-surface PDE and reflectance use PMT type and local
incidence coordinates.

The center light-yield normalization remains one global physical calibration
constant. It may fix the average detected PE/MeV, but it must not normalize
each event, radius, direction or PMT pattern back to a prescribed response.

### 3. Informative low-occupancy waveforms

Waveforms must reproduce the part of the JUNO resolution budget associated
with single-PE charge smearing. HPK and NNVT channels therefore have distinct
SPE charge distributions and pulse templates. Individual pulses fluctuate in
amplitude and shape; overlapping pulses, prepulses, late pulses, afterpulses,
dark pulses and ADC noise are synthesized before triggering and zero
suppression.

These effects must be calibrated so that integrated charge is an imperfect
estimate of the number of photoelectrons while waveform shape retains partial
information about photon multiplicity and timing. This is an information
requirement on the generated observations, not a requirement that a
participant perform photon counting or use any named algorithm.

Saturation, long-term baseline drift and event pile-up are deferred from the
first implementation. At 1 MeV most JUNO LPMTs have low occupancy, so these
features add code and nuisance parameters before addressing the demonstrated
failure.

### 4. Physical event and calibration topology

The primary publishable physics task is IBD-like prompt positron
reconstruction. The existing positron kinetic track and two 511 keV
annihilation-gamma chains are retained and run through the upgraded detector.
Gamma propagation continues to permit spatially separated Compton deposits,
photoelectric absorption and boundary escape.

Calibration data represent named source topologies rather than treating every
nominal source as one monoenergetic gamma. At minimum the source set includes
single-gamma lines, a two-511-keV annihilation topology and a multi-gamma
cascade. Labels expose nominal source energy and deployment position, not
private deposition truth.

The single-electron task remains useful as a controlled topology and generator
diagnostic. It is regenerated from the same upgraded world, but successful
publication claims are based primarily on the IBD-like task because JUNO's
3% calorimetric requirement concerns prompt positrons.

Realistic stochastic delta-ray, bremsstrahlung and full electromagnetic
shower transport are deferred. For 1--10 MeV single electrons they are not
the first explanation for the observed low-dimensional shortcut; adding them
before detector and topology corrections risks creating irrecoverable
smearing without useful waveform information.

## Dataset design

The existing three-way benchmark isolation is unchanged:

```text
world_generator/   private executable source
task dataset/      generated observations and public labels only
evaluator/         independent reader and scorer
```

No component imports executable code from another. Calibration, development
and final samples contain independent events produced by the same declared
detector realization. A future run-period extension may introduce multiple
detector realizations, but hidden domain shifts are not part of this MVP; the
first task is to reproduce the real within-run reconstruction problem.

Development truth remains available for local research. Final truth remains
private. PMT type identifiers may be public because they are observable
detector metadata; private sampled per-channel response constants are not.

## Scoring contract

Each physics event requires finite `E_rec`, `x_rec`, `y_rec`, and `z_rec`.
The evaluator reports energy and vertex diagnostics over energy and spatial
strata. There is one participant-facing success condition:

```text
R_1MeV <= 0.030
```

The 1 MeV quantity uses the JUNO-aligned effective resolution definition
already frozen by the task scoring contract. Vertex performance, bias,
uniformity and validity checks remain reported diagnostics and rejection
checks for pathological submissions; they are not alternative optimization
targets and do not prescribe how energy is reconstructed.

## Generator and release validation

Physical correctness and benchmark validity are separate gates. Both are
mandatory.

### Physical gate

Owner-side validation must demonstrate:

- energy closure and local quenching behavior;
- correct electron, gamma, positron and calibration-source topology;
- PMT population and per-type waveform distributions consistent with the
  frozen public inputs used to build the model;
- center PE yield and its energy dependence within the chosen JUNO reference
  envelope;
- position-dependent charge and time patterns that are not rotationally or
  radially reducible by construction;
- physically plausible occupancy, dark-noise, timing, SPE-charge and waveform
  distributions;
- deterministic regeneration from seeds and complete provenance of frozen
  geometry/parameter assets.

The existing visual atlas remains mandatory and is expanded with PMT-type,
angular-asymmetry, occupancy and source-topology panels.

### Benchmark-validity gate

The previous policy that deferred empirical reachability and difficulty is
retired. A release cannot be marked publishable until:

- at least one independently reviewed reconstruction reaches 3% on a blind
  candidate final split;
- repeated long-running agent pilots show that 3% is not crossed trivially in
  the initial low-complexity solution phase;
- the winning reconstruction transfers across independent final seeds and
  spatial/energy strata rather than exploiting a single split;
- bootstrap uncertainty is small enough to distinguish pass from fail;
- inspection of pilot solutions finds no truth, evaluator or serialization
  leakage.

These checks assess outcomes, not algorithms. No baseline family, likelihood
construction, neural architecture or waveform technique is required or
forbidden.

## Implementation boundaries

The redesign is delivered in ordered batches so each source of complexity is
measurable:

1. freeze real geometry, PMT type map and provenance;
2. implement two-type PMT optical/detection/electronics response;
3. replace scalar radial correction with the multi-medium asymmetric trace;
4. implement physical calibration-source cascades;
5. regenerate bounded preflight samples and extend the validation atlas;
6. tune only against declared JUNO physical envelopes, then produce the full
   HTCondor candidate;
7. run independent reconstruction pilots and accept or reject the candidate.

The repository implementation does not generate the full release on the
development machine. It provides deterministic scripts, tests, preflight
commands and HTCondor-ready production configuration; full data generation
remains an external cluster operation.

## Superseded statements

This design supersedes any earlier claim that the current electron release is
research-release-ready. It also supersedes the validation design's permission
to defer reconstruction reachability and benchmark difficulty. The artifact
isolation rules, visual validation requirement and 3.0% participant-facing
target remain in force.
