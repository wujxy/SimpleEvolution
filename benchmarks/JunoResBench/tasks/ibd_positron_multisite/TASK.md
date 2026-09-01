# JunoResBench — IBD-like Positron Multisite Energy Reconstruction

Reconstruct visible energy from sparse PMT waveforms for prompt positrons.
Each event contains a positron kinetic-energy track and two 511 keV
annihilation gamma cascades. Geometry, labeled calibration waveforms, and a
development split are public; the generator and final truth are hidden.

Implement `Submission.prepare(calibration_path, geometry_path)` once, then
return one finite visible-energy estimate `E_rec` in MeV from
`Submission.predict(event)` for each event.

The sole success target is a JUNO-style fitted
`R_1MeV <= 3.0%`. Continuous controls only validate that the output remains a
finite, monotonic, absolute energy estimator; they are not a second score.

The evaluator streams one hidden event at a time into a mount-isolated worker;
the private data directory and generator are not mounted.
