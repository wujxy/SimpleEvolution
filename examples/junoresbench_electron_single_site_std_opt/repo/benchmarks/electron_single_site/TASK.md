# JunoResBench — Single-Electron Energy and Vertex Reconstruction

Reconstruct 1--10 MeV single-electron events from sparse PMT waveforms.
The supplied data include detector geometry, labeled calibration waveforms,
and a development split with local truth. The hidden generator and its
parameters are not supplied.

Implement `Submission.prepare(calibration_path, geometry_path)` once, then
`Submission.predict(event)` for each event. Every prediction must return
`(E_rec, x_rec, y_rec, z_rec)` as four finite floats in MeV and metres.

Energy is scored by a JUNO-style fitted curve and passes at
`R_1MeV <= 3.0%`. This is not the only acceptance condition: the evaluator
also gates the reconstructed energy scale and vertex bias. Probe-energy peak
means must stay close to the true electron energies, radial vertex bias must
remain small, and vertex resolution must not collapse at higher energies.
The frozen gate values are `|bias(1 MeV)| <= 1.5%`,
`max_E |bias(E)| <= 2.0%`, `max_E |radial_bias(E)| <= 0.20 m`, and
`max_{E>=3 MeV} vertex_RMS(E) <= 1.20 * vertex_RMS(1 MeV)`.

Vertex quality includes the 1-MeV `sqrt(mean(||r_rec-r_true||^2))`, which
passes at the numeric threshold in `public/evaluation_config.json`. All
resolution and bias gates are required.

The evaluator sends hidden events one at a time into a mount-isolated worker;
the private data directory and generator are not mounted.
