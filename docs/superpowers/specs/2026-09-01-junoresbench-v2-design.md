# JunoResBench v2 Design

**Date:** 2026-09-01
**Status:** Approved design; implementation not started

## 1. Purpose

JunoResBench v2 is a single, open-ended reconstruction problem: recover the
visible energy of IBD prompt-like positrons from PMT waveforms in a synthetic
JUNO-like liquid-scintillator detector.

The benchmark must be simple to state and complete to execute. It defines the
scientific background, supplied data, submission contract, score, and one
terminal target. It does not disclose the forward generator, its parameters,
an oracle decomposition, or a preferred reconstruction method. Agents are
expected to inspect the data, form hypotheses about the detector response, and
develop their own reconstruction and calibration model.

The synthetic detector follows JUNO's relevant physical mechanisms and energy
resolution definition. Its numerical parameters are not required to reproduce
the real JUNO detector.

## 2. Single task and success condition

For every event, the submission reconstructs prompt visible energy from sparse
PMT waveforms and documented detector geometry. The true event energy and
vertex are hidden. Vertex reconstruction may be used internally but is neither
a required output nor a separately ranked task.

The sole success condition is

\[
R_{1\,\mathrm{MeV}} \le 3.0\%.
\]

There are no secondary success tiers and no combined energy/vertex/time
ranking.

## 3. JUNO-aligned resolution definition

The evaluation population is prompt-like positrons at multiple fixed kinetic
energies. Each event includes the positron kinetic-energy deposition and both
511 keV annihilation gammas:

\[
E_{\mathrm{dep}} = E_k + 1.022\ \mathrm{MeV}.
\]

The reference energy grid follows the JUNO resolution study:

\[
E_k=(0,\ 0.5,\ 1,\ 2,\ 3,\ 4,\ 5,\ 6,\ 8,\ 11)\ \mathrm{MeV}.
\]

Events are distributed throughout the documented fiducial volume. The
submission does not receive their true vertices.

At each kinetic-energy point, the evaluator fits the reconstructed-energy
distribution with the benchmark's fixed Gaussian fitting procedure and obtains
its mean \(E_{\mathrm{vis}}^i\) and width \(\sigma^i\). It then fits

\[
\frac{\sigma}{E_{\mathrm{vis}}} =
\sqrt{
  \left(\frac{a}{\sqrt{E_{\mathrm{vis}}}}\right)^2 +
  b^2 +
  \left(\frac{c}{E_{\mathrm{vis}}}\right)^2
}.
\]

The scalar score is the full fitted curve evaluated at 1 MeV:

\[
R_{1\,\mathrm{MeV}}=\sqrt{a^2+b^2+c^2}.
\]

Thus 3.0% is not a requirement on the statistical coefficient \(a\), and it
is not the width of a special 1.000 MeV positron sample. It is the standard
1 MeV anchor of the complete positron resolution curve.

## 4. Hidden detector world

The authoritative generator is hidden from submissions. It implements the
following causal chain:

1. A positron loses kinetic energy through a spatial and temporal sequence of
   charged-particle deposition steps.
2. Positron annihilation produces two 511 keV gammas. Gamma interactions
   produce Compton and photoelectric secondaries and hence additional,
   spatially separated charged-particle deposits.
3. Every charged-particle step records position, time, deposited energy,
   particle type, and local stopping power. Electron and positron stopping
   powers use validated tables or an equivalently validated transport model;
   they are not approximated with a heavy-particle Bethe--Bloch formula.
4. Scintillation yield is calculated locally by integrating a Birks-type
   response over each step. There is no event-wide constant quenching scale.
5. Cherenkov production is generated separately from the charged-particle
   kinematics and wavelength-dependent optical properties.
6. Emitted photons undergo vectorized photon-level transport, including the
   enabled wavelength, absorption/re-emission, scattering, boundary, timing,
   and detection effects.
7. Detected photoelectrons pass through PMT and electronics simulation,
   including SPE response, dark noise, timing effects, and digitized waveform
   formation.

Stochastic fluctuations and correlations are introduced at the physical stage
where they arise. The generator must not reproduce them later with an
unmotivated event-level resolution smear.

The expensive generation chain runs offline. Released and final evaluation
data are frozen, so agent iterations perform reconstruction only.

## 5. Data exposed to agents

The public package contains:

- detector and PMT geometry needed to interpret channel identifiers;
- waveform format, units, sampling convention, trigger convention, and event
  boundaries;
- calibration-source data with documented source energy and deployment
  position;
- development physics data sufficient to test a reconstruction program;
- a local evaluator implementing the published score on development truth;
- the submission entry-point contract.

The package does not contain:

- authoritative generator source or detector parameters not required to read
  the data;
- per-step, photon, PE, true-energy, or true-vertex labels for physics test
  events;
- oracle scores, an effect-by-effect resolution budget, or a privileged
  reconstruction implementation.

The final evaluation set is not made available to the submission process as a
bulk unlabeled file. The evaluator runs the submitted reconstruction program
on hidden events. This prevents dataset-level clustering of the discrete
resolution-probe energies.

## 6. Submission and evaluation contract

The submission is an executable reconstruction entry point. For each input
event it returns one finite reconstructed visible energy in MeV. It must return
exactly one value for every event; event rejection is not part of the task.

Final evaluation interleaves the fixed-energy resolution probes with hidden
continuous-energy control events. Control events verify that the output is a
usable calibrated energy response rather than a constant, a lookup of the
probe grid, or a quantized class label. A submission is invalid if it:

- omits events or returns non-finite values;
- depends on observing the final dataset as a whole before reconstructing an
  event;
- has a non-monotonic or locally flat energy response on the continuous
  controls;
- fails the published loose energy-scale consistency check on the controls.

These checks define whether an output is an energy estimator. They are not
additional optimization targets. The implementation specification must publish
their deterministic numerical procedure and tolerance alongside the evaluator
before the benchmark is released.

For every valid submission, the evaluator reports:

- resolution at each fixed energy point;
- fitted \(a\), \(b\), and \(c\);
- the single scalar \(R_{1\,\mathrm{MeV}}\);
- pass or fail against 3.0%.

Only \(R_{1\,\mathrm{MeV}}\) determines success.

## 7. Dataset and runtime shape

Data use a sparse, event-streamable representation. Only channels belonging to
the documented readout selection are stored. Waveforms are chunked and
compressed; final scoring does not require intermediate simulation truth.
Submissions may cache features derived from public calibration and development
data.

There are two evaluation scales:

- a development scale small enough for repeated agent iteration;
- a statistically stable hidden final scale used for acceptance.

The implementation plan will choose event counts from a measured statistical
precision and runtime study, not by copying JUNO's production sample size. As
an engineering target, a development evaluation should complete in roughly two
minutes for the supplied baseline on the reference machine. Offline generation
time is not charged to submissions.

## 8. Benchmark-owner validation

Before release, the benchmark owner validates only what is needed to establish
that the benchmark is sound:

- energy is conserved through the particle-deposition chain within documented
  escape processes;
- quenching varies with charged-particle energy and local stopping power;
- positron annihilation produces the expected spatial and timing topology;
- optical and electronics stage distributions are physically coherent;
- random seeds and frozen data are reproducible without leaking truth;
- the fitted score is statistically stable at the 3.0% decision boundary;
- at least one waveform-only reference method can reach the stated target;
- the simple public baseline does not make the target trivial.

No fixed oracle floor, privileged score such as 2.8%--2.9%, or prescribed
resolution budget is part of the public task or a permanent world requirement.

## 9. MVP exclusions

JunoResBench v2 does not include:

- separate electron, gamma, or mixed-particle rankings;
- vertex or event-time acceptance targets;
- a white-box generator track;
- multiple success levels;
- full Geant4 execution during agent evaluation;
- mechanisms that do not materially affect this energy-reconstruction task;
- explanatory material that reveals a preferred solution.

These exclusions keep the benchmark focused: one physically complete hidden
world, one reconstruction output, one JUNO-aligned metric, and one long-horizon
target.
