---
name: wall-foundation-attack
description: >-
  You are about to claim a floor, ceiling, or impossibility: attack
  the reading of the constraint before the number under it.
tier: research
audience: shared
---

# wall_foundation_attack — attacking the reading before the number

Load this when about to claim a floor, a ceiling, or an
impossibility. Such claims are usually readings of constraints, not
the constraints: the number under a wall is rarely the wall — the
interpretation is. A team that keeps optimizing against a
mis-remembered constraint is climbing a wall that was never there.

## The attack, in order

Return to the original constraint text. For each condition, ask how
much tighter your reading is than the text: where did "within 1e-13"
become "bit-exact", where did "single-threaded" become "no
vectorization", where did "don't change the output" become "don't
change the computation"?

Classify the wall. Physical: mathematics or resources forbid it.
Engineering: the current implementation's structure forbids it.
Cognitive: nobody has thought of the way past. Only the first
deserves "impossible"; the other two are work orders.

Find a counterexample anchor: has anyone, anywhere, gone further
under the same constraint as written? One verified example collapses
the wall — and its absence is the first real evidence the wall
exists.

Commission the attack, don't perform it alone in the framing that
produced the claim: hand a colleague a prove-me-wrong brief — attack
this reading of these constraints — not a verify-me brief. A
colleague briefed to verify will verify.

## If the wall stands

A wall that survives the original text, the classification, and a
search for counterexamples is now evidence-backed. Say so, record it
with its evidence, and stop re-attacking it: the program's effort
belongs elsewhere until new information arrives.

Provenance: the r6 false floor (rel<1e-13 read as bit-exact,
co-signed three times); the r7 counterexample that collapsed it.
