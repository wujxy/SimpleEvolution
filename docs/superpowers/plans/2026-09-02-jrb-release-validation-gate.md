# JunoResBench Release Validation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix noise-driven ROI expansion and make numerical plus visual release validation mandatory before publication.

**Architecture:** The producer derives its ROI threshold from digitizer noise. A serialized-artifact validator invokes the independent waveform audit, combines its metrics with truth-only physics and file-hygiene checks, and writes an owner-side validation bundle with a release marker.

**Tech Stack:** Python 3, NumPy, Matplotlib, pytest

## Global Constraints

- Do not modify the mounted rejected candidate.
- Do not require a baseline or expert/reference reconstruction in this phase.
- Validation figures are mandatory release artifacts.
- Validator and plotting code must not call the simulator.
- Do not expose private validation artifacts to agents.

---

### Task 1: Noise-derived ROI threshold

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/build_task.py`
- Modify: `benchmarks/JunoResBench/tests/test_sparse_waveforms.py`

**Interfaces:**
- Produces: `roi_threshold_adc(wave_cfg, sigma=5.0) -> int`
- Consumes: `noise_sigma_mv`, `lsb_v`

- [x] Add tests asserting the default threshold is 29 and scales with noise and ADC LSB.
- [x] Run the tests and observe failure because the helper does not exist.
- [x] Implement `ceil(sigma * noise_sigma_mv * 1e-3 / lsb_v)` and replace literal `6` in production.
- [x] Run sparse-waveform tests and require PASS.

### Task 2: Machine-readable waveform gates

**Files:**
- Modify: `benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py`
- Modify: `benchmarks/JunoResBench/tests/test_electron_waveform_figures.py`

**Interfaces:**
- Produces summary keys: `sparse_to_stored_dense_ratio`, `charge_energy_correlation`, and `time_distance_slope_ns_per_m`.

- [x] Add assertions for the three finite metrics.
- [x] Run the focused test and observe failure for absent keys.
- [x] Compute sparse/dense ratio from selected event rows, Pearson charge-energy correlation, and within-event centered timing-distance slope.
- [x] Run the focused test and require PASS.

### Task 3: Mandatory validation bundle

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/validate_release.py`
- Modify: `benchmarks/JunoResBench/tests/test_release_validation.py`

**Interfaces:**
- Produces: `validate_release(task_name, release_root, output_root, sample_limit=32) -> dict`
- Produces: `validation_report.json`, `README.md`, sixteen PNGs, and exactly one state marker.

- [x] Build a tiny serialized electron candidate fixture and assert a valid candidate gets `ACCEPTED` without evaluator/baseline/reference arguments.
- [x] Mutate the ROI metrics and assert rejection with explicit gate names.
- [x] Implement structure, hygiene, physics, atlas-presence, ROI, compression, charge, and timing gates.
- [x] Render Markdown with all figure links and PASS/REVIEW/FAIL descriptions.
- [x] Ensure validation exceptions produce a report and `REJECTED`, not partial acceptance.
- [x] Run validation tests and require PASS.

### Task 4: Production workflow and rejected-candidate evidence

**Files:**
- Modify: `benchmarks/JunoResBench/world_generator/condor/README.md`
- Modify: `benchmarks/JunoResBench/README.md`
- Modify: `benchmarks/JunoResBench/docs/JunoResBench_two_tier_design_report.md`
- Create: `benchmarks/JunoResBench/validation/electron_single_site_current/*`

**Interfaces:**
- Consumes: current mounted electron candidate read-only.
- Produces: reproducible preflight/full validation commands and a checked-in rejected-candidate report.

- [x] Document the small preflight command before full HTCondor submission.
- [x] Run the validator against the current mounted candidate and require nonzero exit plus `REJECTED` for ROI gates.
- [x] Verify the report embeds all sixteen figure links and contains no waveform copy.
- [x] Run related JunoResBench tests and `git diff --check`.
- [x] Commit locally without push.
