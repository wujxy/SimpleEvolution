# XSBench human-expert reference (hidden — do NOT expose to the agent)

This directory is the **reference resource** for the xsbench_opt task. It is a
sibling of `repo/` and is deliberately **outside** the repository the Scientist
and Executor see. If you copy this task elsewhere, keep `reference/` out of any
path the agent can read.

The agent's baseline (`repo/`) has all upstream optimized kernels and reference
checksums removed. Everything in here is what "a human expert already knew":

- `xsbench-official-openmp-threading/` — the unmodified upstream CPU
  implementation, including the author-written optimized event kernel
  (`run_event_based_simulation_optimization_1`, selected upstream with `-k 1`):
  sample/XS-lookup kernel splitting + a parallel key-value quicksort by
  material and energy, to improve cache locality and index reuse.
- `RESULTS.md` (this file) — the measured human-expert bar on this machine and
  the published literature.

## Method

1. Build the **official** `openmp-threading` source (identical to the agent's
   baseline except it keeps the optimized kernels) inside the same Apptainer
   runtime image the harness uses (`../apptainer.sif`), with the same frozen
   compiler/flags.
2. Run the identical frozen benchmark config the harness uses:
   `-m event -s small -G unionized -t 1 -l 2000000`.
3. Measure:
   - `-k 0` — upstream baseline kernel (controls: should match the agent's
     baseline FOM).
   - `-k 1` — the author's optimized kernel = the **human-expert bar**.

## Human-expert bar (measured 2026-08-19 on user-Super-Server, gcc 11.5.0, in apptainer.sif)

Frozen config: `-m event -s small -G unionized -t 1 -l 2000000`. Median of 5
runs in a single session (the host is shared and noisy — absolute numbers vary
±20-50% across sessions; the *ratio* within one session is the reliable
comparison).

| kernel | verification checksum | median runtime (s) | lookups_per_sec |
| ------ | --------------------- | ------------------ | --------------- |
| upstream baseline (`-k 0`) | 998920 | 2.133 | 937,647 |
| **author optimized (`-k 1`)** | 998920 | 1.094 | 1,828,154 |

Speedup of the author optimized kernel over the upstream baseline (same
session): **1.95×**. In a second, quieter session the same pair measured
1.360 s vs 0.779 s (1.75×). The agent's baseline (`repo/`) is the same code as
`-k 0` and measured 1.273M lookups/s (median 1.571 s) in the reference session;
treat the exact absolute FOM as machine-load-dependent and the ~1.8-2× expert
gain as the robust signal.

The agent baseline (repo/) should reproduce the same upstream-baseline
checksum and a comparable `lookups_per_sec`; a SimpleEvolution frontier node
that beats the `-k 1` row on this machine has surpassed the published reference
implementation.

## Published XSBench optimization literature

- **XSBench paper (definition + initial results)** — J. R. Tramm, A. R.
  Siegel, T. Islam, M. Schulz, "XSBench - The Development and Verification of a
  Performance Abstraction for Monte Carlo Reactor Analysis", PHYSOR 2014.
  https://www.mcs.anl.gov/papers/P5064-0114.pdf
- **Many-core optimization** — the upstream repo's own optimized kernels
  (sorted event kernel for OpenMP and CUDA) are the canonical example of the
  human optimization approach for this kernel: reduce thread divergence and
  improve cache re-use by sorting sampled lookups by material then energy.
- **NUMA / page-size optimization** — S. Yoshii, J. R. Tramm, A. Siegel, P.
  Beckman, "Improving the scalability of neutron cross-section lookup codes on
  multicore NUMA system", 2019 (arXiv:1909.03632). Finds the kernel is
  memory-latency/TLB bound on Intel NUMA nodes; page-size optimization gives
  ~1.5× over the non-optimized version, and NUMA-aware placement lifts scaling
  efficiency from ~70% to ~95% at 16 threads.
- **Roofline/bandwidth characterization** — on an Intel Xeon Platinum 8180M,
  `calculate_macro_xs` is ~97% of runtime (1 thread) and the lookup kernel is
  DRAM-bandwidth-bound as threads scale.
- **ECP XSBench page** (further measurements):
  https://proxyapps.exascaleproject.org/xsbench/

Bottom line for the human bar: an expert makes this memory-bound kernel faster
by (a) restructuring the event-based kernel so lookups are sorted and reused
(the upstream `-k 1`), (b) reducing TLB/memory-latency pressure (page size,
NUMA), and (c) choosing cheaper acceleration structures. The benchmark freezes
the unionized-grid config and single-thread mode, so (a) is the dominant lever
an expert would apply here.
