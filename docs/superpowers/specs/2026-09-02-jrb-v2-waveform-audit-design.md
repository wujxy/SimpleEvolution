# JunoResBench v2 waveform audit design

## Purpose

Produce a human-readable visual acceptance package for the frozen
single-electron v2 release.  The figures must inspect the waveform files that
agents actually receive, rather than regenerating events or relying only on
generator truth.

## Data boundary

The plotting program reads these release artifacts in place:

- `public/dev/index.npz` and memory-mapped `public/dev/segment_samples.npy`;
- `public/dev/metadata.json` and `public/detector_geometry.npz`;
- `public/dev/truth.npz` for labeled diagnostic correlations;
- `private/truth.npz` only for offline benchmark-owner checks such as `t0` and
  transport topology.

It must not copy waveform storage, materialize the complete split, or call the
world generator.  Event selection is deterministic and bounded.  Dense
waveforms may be constructed only for the PMTs of a few selected events.

## Figures

The output directory is
`benchmarks/JunoResBench/figures/electron_single_site_v2/waveform_audit/`.
The audit contains these sixteen PNGs, grouped into four questions:

1. **Population and position:** `vertex_distribution`,
   `energy_radius_coverage`, and `radial_light_yield`.
2. **Hit pattern:** `hit_pattern_comparison`, `charge_pattern_comparison`,
   `hit_multiplicity_vs_energy`, `charge_vs_energy`, and `event_anatomy`.
3. **Timing:** `first_hit_time`, `time_vs_distance`,
   `tof_corrected_residual`, and `timing_vs_radius`.  Because stored samples
   are trigger-relative, the audit must explicitly report if the release lacks
   the trigger time needed to combine private `t0` with the waveform clock;
   it must not plot a physically invalid subtraction.
4. **Waveform/electronics:** `waveform_examples`, `waveform_overlays`,
   `pulse_integral_vs_peak`, and `roi_structure`.

Every figure title or report entry states what physical behavior it checks and
the expected qualitative signature.  Quantitative summaries are written to a
small JSON file so tests and the report do not infer values from pixels.

## Implementation

Add one focused script alongside the existing truth diagnostic script.  A
small event reader slices only the selected event's segment and sample ranges
from the memory map.  Shared plotting helpers handle PMT sky coordinates,
per-channel waveform reconstruction, charge integration, and timing
summaries.  No new benchmark or runtime interface is introduced.

Extend the design report with an index of generated figures, their physical
interpretation, and any suspicious observations.  Keep the existing five
truth/transport figures; the waveform audit complements rather than replaces
them.

## Verification

Tests use a tiny synthetic sparse release to verify:

- the expected figure and JSON outputs are created;
- event reads remain bounded to selected sample ranges;
- plots use the release artifacts and never import/call `world_generator`;
- the script handles empty-hit events and malformed indices explicitly.

The real release run is accepted only after all PNGs are generated and
visually inspected.  The final report records measured diagnostics and any
remaining simplifications instead of asserting correctness from test success
alone.
