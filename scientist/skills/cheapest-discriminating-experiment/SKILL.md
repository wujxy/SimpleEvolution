---
name: cheapest-discriminating-experiment
description: >-
  Two explanations both fit the evidence and the choice is live: find
  the cheapest observation they predict differently, and freeze
  predictions before looking.
tier: research
audience: shared
---

# cheapest_discriminating_experiment — separating explanations for the cost of one look

Load this when two (or more) explanations both fit the evidence and
the choice between them is live. Investigating either deeply is a
bet; an observation the explanations predict differently settles the
question for the cost of one look. The discriminating observation is
almost always cheaper than any single deep investigation of either
branch.

## Designing it

Write each explanation's discriminating prediction: the observation
under which A is right and B wrong, and vice versa. If you cannot
write the fork, you do not yet have two explanations — you have one
explanation with a decoration; say so and simplify.

Search for the fork in cost order:

1. data already collected — can existing records, logs, or artifacts
   differ under A vs B?
2. a cheap probe — one small run, one instrumented point, one
   calculation;
3. only then, a new experiment.

Freeze predictions before looking. Declare in advance: "if we see X,
A is dead." A prediction written after the observation explains
everything and forbids nothing — the discipline is the declaration,
not the data.

## After

One explanation dies, or both do (then the fork was miscalculated —
rare, and informative). The survivor is not yet confirmed; it is
merely unrefuted at this fork, and the work of building on it
proceeds with that accounting.

Provenance: textbook experimental design; the r6 searcher probe that
settled a framing dispute in one measurement.
