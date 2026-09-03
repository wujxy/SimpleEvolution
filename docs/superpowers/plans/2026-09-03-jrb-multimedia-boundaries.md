# JunoResBench Multi-medium Boundary Implementation Plan

**Goal:** Replace the trace-mode single optical sphere with a minimal causal
LS--acrylic--water model whose interfaces and fixed structures create
wavelength-, angle-, path- and detector-coordinate-dependent observations.

**Scope discipline:** This batch owns bulk media, dielectric boundaries and
fixed analytic occluders only. PMT glass/photocathode reflection, electronics,
calibration datasets and evaluator changes remain later batches.

## Task 1: Pure boundary optics

Add tests first for unpolarized Fresnel probability conservation, normal
incidence, Snell refraction and total internal reflection. Implement vectorized
medium refractive/group-index and absorption tables plus one vectorized
dielectric-interface operation. Keep this module independent of detector state.

## Task 2: Concentric multi-medium trace

Add tests proving a direct photon crosses LS--acrylic--water, accumulates the
piecewise group delay, preserves unit direction and can reflect/TIR at an
interface. Refactor Stage 3 into a medium-state loop over analytic concentric
spheres. LS retains absorption/re-emission/Rayleigh; acrylic and water receive
their own wavelength-dependent absorption and propagation time. A reflected
photon remains active in the incident medium; a transmitted photon changes
medium.

## Task 3: Fixed analytic structures

Add tests proving the structure map is deterministic in detector coordinates,
rotating an otherwise identical ray can change its transmission, and disabling
structures restores the unmasked path. Implement only three inexpensive public-
geometry-constrained approximations: the top chimney cap, a deterministic
acrylic-node lattice and broad support/mask bands. Mark their unresolved sizes
and optical depths as synthetic rather than JUNOSW constants.

## Task 4: Physical anchors and release lock

Recalibrate only the single trace center-yield normalization if the explicit
interfaces shift it outside the existing 5% anchor. Update the generator physics
document and deterministic golden digests after all physical tests pass. Run:

```bash
python -m pytest benchmarks/JunoResBench/tests/test_boundaries.py -q
python -m pytest benchmarks/JunoResBench/tests/test_trace.py -q
python -m pytest benchmarks/JunoResBench/tests -q
git diff --check
```

Commit each task separately and fast-forward merge to `main` only after the full
suite passes both before and after merge.
