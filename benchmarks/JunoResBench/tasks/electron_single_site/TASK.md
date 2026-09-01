# JunoResBench — Single-Electron Energy and Vertex Reconstruction

Reconstruct 1--10 MeV single-electron events from sparse PMT waveforms.
The supplied data include detector geometry, labeled calibration waveforms,
and a development split with local truth. The hidden generator and its
parameters are not supplied.

Implement `Submission.prepare(calibration_path, geometry_path)` once, then
`Submission.predict(event)` for each event. Every prediction must return
`(E_rec, x_rec, y_rec, z_rec)` as four finite floats in MeV and metres.

Energy is scored by a JUNO-style fitted curve and passes at
`R_1MeV <= 3.0%`. Vertex quality is the 1-MeV
`sqrt(mean(||r_rec-r_true||^2))` and passes at the numeric threshold in
`evaluator/task_config.json`. Both conditions are required.

The evaluator sends hidden events one at a time into a mount-isolated worker;
the private data directory and generator are not mounted.
