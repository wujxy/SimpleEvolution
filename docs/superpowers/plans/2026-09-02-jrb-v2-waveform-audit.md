# JunoResBench v2 Waveform Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Generate and document sixteen human-inspectable figures from the frozen v2 electron waveform release.

**Architecture:** One standalone owner-side script loads small index/truth arrays and memory-maps waveform samples. It computes bounded per-event summaries, renders sixteen fixed figures, and writes the plotted numerical diagnostics to JSON.

**Tech Stack:** Python 3, NumPy, Matplotlib, pytest

## Global Constraints

- Read the frozen release in place; never copy waveform storage.
- Never call or import `world_generator` from the plotting script.
- Select events deterministically and materialize only selected event channels.
- Produce exactly the sixteen filenames frozen in the design specification.

---

### Task 1: Bounded release reader and plot contract

**Files:**
- Create: `benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py`
- Create: `benchmarks/JunoResBench/tests/test_electron_waveform_figures.py`

**Interfaces:**
- Produces: `ReleaseWaveforms(path: Path)` and `read_event(index: int) -> EventWaveforms`
- Produces: `build_waveform_figures(release_root: Path, output_dir: Path) -> dict[str, Path]`

- [x] Write a synthetic two-event sparse release fixture and assert exact output names, nonempty PNGs, and `summary.json`.
- [x] Run `python -m pytest -q benchmarks/JunoResBench/tests/test_electron_waveform_figures.py` and observe failure because the module does not exist.
- [x] Implement index validation and event-local sample slicing; reject invalid offsets and return empty arrays for empty events.
- [x] Add an AST/source assertion that the script contains neither `world_generator` nor file-copy calls.
- [x] Run the focused test and require PASS.

### Task 2: Sixteen real-release figures

**Files:**
- Modify: `benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py`
- Modify: `benchmarks/JunoResBench/tests/test_electron_waveform_figures.py`
- Create: `benchmarks/JunoResBench/figures/electron_single_site_v2/waveform_audit/*.png`
- Create: `benchmarks/JunoResBench/figures/electron_single_site_v2/waveform_audit/summary.json`

**Interfaces:**
- Consumes: event-local segment arrays and PMT geometry from Task 1.
- Produces: the sixteen filenames listed in the approved design.

- [x] Test that all sixteen names are returned and the summary records selected event indices and bounded sample counts.
- [x] Implement vectorized summaries for all events from index offsets plus bounded waveform summaries from a deterministic sample of at most 96 events.
- [x] Implement position, hit-pattern, timing, and electronics plots with physical expectation text in titles.
- [x] Run the focused synthetic test and require PASS.
- [x] Run the script against `/home/wujxy/mnt/lustrefs_juno26/users/lidian/jrb_v2/production/electron_single_site/release` and require sixteen nonempty PNGs plus JSON.
- [x] Inspect every PNG for empty panels, pathological axes, or contradicted physical signatures.

### Task 3: Human acceptance report and regression verification

**Files:**
- Modify: `benchmarks/JunoResBench/docs/JunoResBench_two_tier_design_report.md`
- Modify: `benchmarks/JunoResBench/README.md`

**Interfaces:**
- Consumes: `summary.json` and visually inspected figures.
- Produces: a figure-by-figure physical interpretation and explicit anomaly list.

- [x] Add a compact table mapping every figure to its question, expected signature, and observed result.
- [x] Link the audit directory and generation command from the benchmark README.
- [x] Run waveform-figure, truth-figure, isolation, evaluator, and generator tests.
- [x] Run `git diff --check`, review staged paths, and commit locally without push.
