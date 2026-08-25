# tide-research — an investigation demo (knowledge lives outside the weights)

The XSBench demos test optimization craft on a self-contained world.
This one tests **investigation**: the world ships only a research
brief; everything the task needs — the station's harmonic constants,
the prediction equation's operational conventions, the units/datum
pitfalls, the official curve to self-check against — lives in the
published online record, and the machine has internet.

The deepseek researcher model's cold prior on this domain was probed
(`scripts/probe_cold_knowledge.py --set tides`): it states the
equation skeleton correctly but carries wrong nodal-correction
coefficients, a confabulated epoch convention, and half-invented API
fields. Note, though, that the lunar node sat near its zero in the
demo window (2025-06), so nodal-table errors specifically are
dormant there; the live mines are the argument/time conventions, the
GMT-vs-local phases (the API returns both), feet-vs-meters, the datum
offset, and plain interval arithmetic (6 min = 360 s, not 600).

## Layout

- `world/` — the world tree the seat sees: `task.md` and nothing
  else. Git-tracked; `spec.json` names the base commit.
- `reference/` — harness-side, outside the world:
  - `harcon-9414290.json` — frozen snapshot of the station's
    published harmonic constants (mdapi),
  - `official-predictions-20250610-16.json` — frozen snapshot of the
    official 6-minute predictions for the gate window,
  - `grade.py` — the gate (stdlib only),
  - `authority-uptide.md` — what a correct independent implementation
    (uptide + the frozen public constants) achieves on this window:
    demeaned RMS 4.1 cm, max 10.3 cm, corr 0.998 — that is what
    calibrates the tolerances.

## Probe findings (2026-08-25, deepseek-v4-flash @ temp 1.0)

Response probes with the full production context on this world
(`scripts/probe_oneworld.py --spec runs/tide-probe-1/spec.json`):

- cold start ×5: 1/5 opens with `consult` (asking precisely the right
  research questions — constituent count, argument conventions,
  datum handling, official quantization); the rest ground first
  (read task.md, inspect the machine) — also correct behavior.
- epistemic-interview ×3: 2/3 produce a full honesty-graded self-
  assessment — equation SURE, nodal coefficients HALF-REMEMBER "do
  not trust these digits", V0 epoch convention DO NOT KNOW "must
  obtain, not recall" — and name web search through the assistant as
  the acquisition channel. The same model asked cold
  (probe_cold_knowledge --set tides) states wrong coefficients
  confidently: calibration is a function of context, which is the
  demo's premise working as designed.
- operational: a deep multi-part consult ran past the 900 s box but
  finished well inside 1800 s on this endpoint, returning correct
  Schureman Table 15 coefficients with a numeric self-check against
  the closed forms; single-question consults finish in 2-3 min. The
  example spec therefore carries consult_timeout_seconds=1800.

## Running

Copy the world (keep the example pristine), write a run spec from
`spec.json` (fill the two API-key placeholders), launch the scientist
CLI against it. The run environment must carry the HTTP(S) proxy so
the world's shell and the assistant both reach the web.
