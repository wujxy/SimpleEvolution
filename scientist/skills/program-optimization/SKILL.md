---
name: program-optimization
description: >-
  A closed speed/efficiency objective over a code world with an
  executable evaluator and frozen correctness gates: how this kind of
  research runs — the whole goal to a strong Executor early, your own
  verification in parallel.
tier: task
audience: shared
---

# program_optimization — how optimization research runs

Load this when the task is a closed optimization: an objective
number with a direction, an executable evaluator, frozen correctness
gates, a measurable baseline — all over a code world. This is how
mature performance researchers run this kind of program, and it is
not a solo craft: the decisive fact about such tasks is that a
strong Executor can carry the whole loop — read the code, profile,
hypothesize, change, measure, iterate — end to end.

## The opening

Minimum orientation, and no more: the objective (which number, which
direction), the gates (their exact text — the original, not anyone's
summary), the evaluator and its cost tiers (probe vs authoritative),
the baseline (measured, with its spread), and the assets (git
history, research memory, prior art). This orientation is for the
brief, not for your understanding of the code.

If objective + evaluator + gates + baseline close the problem — they
usually do, that is what "closed" means — hand the whole optimization
goal to an Executor early: one engagement, the whole goal, a long
fuse. What you still want to check — the benchmark's honesty, a
mechanism, the history — you do in parallel, alongside the running
engagement. Your understanding of the implementation is not a
precondition for their start; it catches up.

The brief carries facts, not a route: objective, gates verbatim,
baseline with spread, evaluator economics, the known facts (any
existing profile, dead lanes from memory), and stop conditions.
Candidate directions of yours, if you have them already, travel as
ideas — a strong colleague reads them as priors, never as plans.

## The craft of this kind

- Baseline and instrument first: repeats on a fixed state, the noise
  floor that defines ties, the probe/authoritative tiering
  (measurement-discipline).
- Profile before guessing — and read the profile structurally:
  per-call cost, call count, and architecture are different diseases
  with different cures. Beyond the hotspots, look at invariant work
  (computable before the loop), memory layout, and the algorithm's
  structure itself.
- The gates stay frozen; speed comes from the algorithm, never from
  parallelism smuggled past a single-threaded pin.
- A surprising speedup is a claim, not a result (critical-validation)
  — check the timed region, the denominator, the units, before it
  enters the record.
- Two performance explanations coexist: one cheap observation they
  predict differently, not two deep investigations
  (cheapest-discriminating-experiment).
- Local improvements flatten: the framing is the suspect — open
  genuinely different directions (research-expansion), and before any
  floor is claimed, attack the reading of the constraint, not the
  number (wall-foundation-attack).

## The arc

A ratchet: every gate-passing improvement banks as a commit, the tree
stays clean, dead ends get recorded. The recurring central judgment
of this kind of research is the plateau question — is this the
floor, or is the framing spent? — and it is a judgment, not a
measurement: treat every floor claim, including your own, as a claim.
