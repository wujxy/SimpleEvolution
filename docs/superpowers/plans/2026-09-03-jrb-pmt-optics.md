# JunoResBench PMT Optical Closure Plan (Batch 3A)

**Goal:** Close the water--PMT optical branch before changing electronics:
retain photocathode landing coordinates, make surface reflection depend on PMT
type/wavelength/incidence, return reflected photons to water, and make PE
collection depend on the same arrival state.

## Task 1: PMT optical response functions

Write failing tests for bounded type-dependent reflectance, grazing-angle
increase and fixed local collection maps. Implement synthetic analytic response
families constrained by public HPK/dynode versus NNVT/MCP differences; do not
read JUNOSW performance tables.

## Task 2: Trace reflection and landing contracts

Extend `S3Output` with normalized photocathode radius and local azimuth. At an
actual PMT-disc intersection, sample reflection before Stage 4. Reflected rays
remain active in water and continue with their path time; non-reflected rays
carry final direction, wavelength and landing coordinates into Stage 4.

## Task 3: Type/landing-dependent PE collection

Use PMT model, incidence and landing coordinates together in Stage 4. Preserve
the existing private per-tube PDE factor and type-isolated RNG order. Refit only
the single global trace normalization if the center-yield anchor moves outside
its registered tolerance.

## Task 4: Lock and verify

Update physical documentation and reviewed trace golden digests. Run PMT,
Stage-4, trace and full JunoResBench tests before and after fast-forward merge.
Waveform/electronics behavior is explicitly unchanged in 3A.
