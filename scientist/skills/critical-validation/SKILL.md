---
name: critical-validation
description: >-
  A result is about to become a conclusion — especially one
  surprisingly good, bad, or out of pattern: validate in proportion
  to the surprise.
tier: research
audience: shared
---

# critical_validation — the standard a surprising result must meet

Load this when a result is about to become a conclusion — especially
one that is surprisingly good, surprisingly bad, or out of pattern.
The standard scales with the surprise: routine numbers from a trusted
instrument need a spot check; a result that would change the program
needs the ladder. Surprising-good and surprising-bad are the same
audit.

## The ladder, cheapest rung first

1. Replicate — same state, fresh run. A number that does not survive
   its own repetition was never a result.
2. Check the measurement: units and scale; the timed or observed
   region (did work move out of it?); whether the gate actually ran;
   the denominator (a smaller or easier sample inflates the value);
   selection (is this the best of N runs reported as the result of
   one?).
3. Check the controls — what should not have moved, didn't (see
   controls-and-confounders).
4. Check the alternative explanations: artifact, leakage, contamination
   — the boring explanations, in order of how often they are the
   answer.
5. Mechanism consistency: name where the effect physically comes
   from. An effect whose magnitude has no mechanism defaults to
   measurement error, however well-replicated.
6. Independent validation: a different channel or method reproducing
   the claim. This is the only rung that upgrades "I measured it" to
   "it is there".

## The disposition

None of this is hostility to the result — a surprising result that
climbs the ladder is the most valuable thing a program produces. The
ladder exists so that when the result stands, it stands on evidence
rather than on hope, and when it falls, it falls cheaply.

Provenance: RATE_PLAUSIBLE's design (timing cheats that pass bit-exact
gates); ML leakage canon; physics excess checks.
