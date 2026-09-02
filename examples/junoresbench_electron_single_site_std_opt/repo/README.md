# JunoResBench single-electron reconstruction

Reconstruct energy and vertex from sparse PMT waveforms. The public package is
under `benchmarks/electron_single_site/`; its `TASK.md` defines the benchmark
and its data link is read-only. Only `src/` is editable.

The objective is the fitted `R_1MeV`, with a single target of 3.0% or better.
The frozen vertex threshold must also pass. No event-time output is requested.

Run `bash scripts/check_verify.sh` to validate the four-array prediction
contract and `bash scripts/bench.sh` to measure the public development score.
The starting solver integrates negative FADC charge, calibrates energy on the
labeled source sample and fits an affine charge-centroid vertex correction.
It is deliberately a baseline for continued research.

The sparse waveform files are large and externally mounted. Stream events;
do not materialize the full sample array. `/scratch` is writable and suitable
for reusable features or fitted models.
