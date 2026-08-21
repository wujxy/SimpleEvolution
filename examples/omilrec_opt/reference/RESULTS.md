# OMILREC human-expert reference (hidden — do NOT expose to the agent)

This directory is the **reference resource** for the omilrec_opt task. It is a
sibling of `repo/` and is deliberately **outside** the repository the Scientist
and Executor see. If you copy this task elsewhere, keep `reference/` out of any
path the agent can read.

Unlike xsbench_opt, the OMILREC bench ships no bundled "answer": the target
repo contains only the clean v1.0.0 algorithm, and its gates are the frozen
truth (not an implementation to copy). The human-expert knowledge lives in the
sibling `omilrec/` workspace and is summarized here.

## The bar to beat

The frozen v1.0.0 performance baseline (recorded in `repo/baseline/manifest.json`,
measured on this machine, single-threaded, 100 events × 3 repetitions,
SniperProfiling mean ms/evt):

| quantity | value |
| -------- | ----- |
| median ms/evt | **919.95** |
| min / max | 895.57 / 921.48 |
| config | 100 events, 1 thread, J26.1.1, input `index_12628_rtraw_1.json` |

A SimpleEvolution candidate must pass the four frozen gates (CONTRACT, FCN
1e-13, CONSISTENCY 4mm/7keV/10ps/0.1PE, SINGLE_THREADED) **and** land below
this ~920 ms/evt bar to be a genuine improvement.

## Human-expert trajectory (sibling `omilrec/` repo)

The workspace at `/datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/omilrec/`
holds the full optimization history v1.0.1 → v1.12.1 (per-commit gates: drift
≤ FP-noise floor, speed ratchet). Its `benchmarks/speed.csv` records this
machine's later versions (Intel Xeon Platinum 8358P, **10 events**):

| commit (era) | ms/evt (this machine) |
| ------------ | --------------------- |
| v1.10.2-era `5b07c92` | 267.8 |
| v1.10.2-era `a276d24` | 217.5 / 221.8 |
| v1.10.2-era `04af0a0` | 213.8 |

**Caveat**: these were measured at **10 events** with the then-current
methodology, not this bench's exact 100-event config, so treat them as an
order-of-magnitude expert target (~3-4× below the v1.0.0 baseline), not a
drop-in comparable FOM. The relevant question for a SimpleEvolution candidate
is whether it can reproduce that gain while passing **this** bench's v1.0.0
gates (later versions may have moved off the v1.0.0 numerics).

## Known-safe / known-unsafe optimization catalog

Summarized from the `omilrec-optimize` skill (sibling workspace, v1.0.1 →
v1.10.1 lessons). **Known-safe** patterns that preserve the v1.0.0 FCN to the
1e-13 gate:

- Algebraic simplification of the likelihood (precompute repeated
  sub-expressions; Kahan summation for the firing-LL accumulation).
- Precomputing reciprocals / per-event constants once instead of in the hot
  loop.
- `bulk-memcpy` / hoisting the loading phase (input → likelihood state) out of
  the per-event hot path.
- Minuit2 loop/stepping hygiene that does not change function values.
- Data-layout / memory-access reordering that does not change arithmetic.

**Known-unsafe** (will fail the 1e-13 FCN gate or the E2E ranges):

- Rewriting the likelihood with a different summation order that crosses the
  FP-noise floor on the 4 fixed FCN events.
- Threading or vectorization that changes per-event results.
- Touching the gate/tests/scripts/baseline to relax the comparison.

The FCN gate is deliberately strict (relative LL error < 1e-13 against
`tests/fixtures/v100/fcn_points.tsv`), so only optimizations that keep the
v1.0.0 arithmetic equivalent to the FP-noise floor are viable. The E2E gate
(4 mm / 7 keV / 10 ps / 0.1 PE over 18 events) is the backstop that catches
behavioral changes the FCN probe cannot see.

## Published context

- OMILREC = the OMILREC V2 macroscopic likelihood reconstruction used in JUNO
  (the likelihood vertex/energy fitter driven by `Calculate_EVLikelihood`).
  The benchmark freezes the v1.0.0 algorithm and single-thread mode, so the
  dominant expert lever is reducing the likelihood kernel's per-event cost.
