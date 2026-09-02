# JunoResBench release validation gate design

## Goal

Make physical and visual validation a mandatory release artifact.  A user must
be able to understand how a generated benchmark looks without running an
agent or reading generator source.

This phase does **not** require an expert reconstruction method to reach the
3.0% target.  Expert/reference reconstruction and empirical reachability are
deferred until Scientist and Coding Agent long runs exist.  The validator must
not invent a reviewed reference merely to satisfy the old checklist.

## Release states

Generation first creates a `candidate` directory.  The independent owner-side
validator reads the completed files and writes:

```text
validation/
  validation_report.json
  README.md
  figures/*.png
  ACCEPTED | REJECTED
```

Only `ACCEPTED` candidates may become the path mounted as the official
release.  Validation files remain owner-side and are not included in the
agent-visible `public` tree.

## Independence

The validator reads serialized public/private artifacts.  It does not call
the simulator to regenerate comparison events and does not import evaluator
implementation.  Checks computed from final waveforms are preferred over
self-consistency checks using generator truth.  Truth-only checks such as
energy conservation are clearly labeled.

This production QA lives under `world_generator/validation`; it is not a
fourth agent-visible benchmark component.  The external contract remains
data plus evaluator plus task world.

## Mandatory machine gates

The report has one overall `release_ready` boolean and explicit failures.
Initial hard gates are:

- no executable files in generated public/private data trees;
- event counts and sparse offsets are structurally valid;
- maximum truth energy-closure error is below `1e-8 MeV`;
- mean local visible fraction below 50 keV is smaller than that at
  0.5--2 MeV;
- all sixteen approved waveform-audit PNGs and their numerical summary exist;
- ROI start-at-zero fraction is below `0.20`;
- ROI length at least 90% of the window occurs in fewer than `0.05` of ROIs;
- stored sparse samples are below `0.35` of the corresponding stored-channel
  dense representation;
- waveform-derived charge is finite and positively correlated with true
  energy in the bounded audit sample;
- signal-selected first-pulse time has a positive distance dependence after
  controlling it per event.

Exact JUNO agreement is not required.  The thresholds reject internally
unreasonable behavior while leaving intentionally simplified detector physics
possible.  Every gate records its value, limit, rationale, and PASS/FAIL.

## Mandatory human atlas

The sixteen existing position, hit-pattern, timing, waveform, and ROI figures
are required release output.  The validator generates a Markdown index that
embeds every figure and states:

- what physical effect the figure checks;
- what qualitative signature is expected;
- the observed numerical result;
- PASS, WARN, or FAIL.

Figures are necessary but not sufficient: machine gates catch subtle or
large-sample failures, while figures catch wrong shapes and correlations that
single numbers hide.  A release cannot pass when either the hard gates fail or
the atlas is absent.

## ROI correction

The generator must derive the sparse threshold from the configured electronics
noise rather than use the literal 6 ADC counts:

```text
sigma_adc = noise_sigma_v / ADC_LSB
threshold_adc = ceil(5 * sigma_adc)
```

For the current 0.35 mV noise and 1 V/14-bit ADC this gives 29 counts.  The
stored integer threshold is therefore 29, not an independently tuned score
parameter.  Pre/post padding remains 16/48 samples.  The validator measures
the resulting efficiency and compression; it does not assume the formula
guarantees success.

## Preflight before HTCondor production

Before a full job, generate a small candidate containing 1, 5, and 10 MeV
coverage at center-like and edge-like positions, plus a small continuous
control sample.  Run the complete validator and inspect its atlas.  Full
production is allowed only after preflight acceptance.  The same validator is
then rerun on the full candidate.

The current mounted electron release is retained as a rejected diagnostic
candidate.  It must not be silently edited in place; corrected waveforms
require regeneration.

## Deferred gates

The following are explicitly deferred, not treated as passing:

- expert reconstruction reaches `R_1MeV <= 3.0%`;
- public baseline stays above 3.0%;
- private reviewed reference reaches 3.0%;
- bootstrap stability of the achieved reconstruction score.

The analytic photoelectron resolution budget remains documented as design
evidence, but it is not a substitute for a future empirical reconstruction
result.
