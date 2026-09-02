# measurement_discipline — numbers that can carry a decision

Load this whenever a number is about to be compared, banked, or acted
on. A number without its uncertainty is a rumor; most research errors
are explanations of deltas the instrument cannot resolve.

## The instrument first

Before trusting any comparison, measure the instrument's own noise:
repeat a fixed state and learn its spread. The spread defines what
counts as a tie — a delta inside the noise floor is a tie regardless
of its direction, and a "trend" assembled from sub-noise deltas is a
story, not a measurement. When repeats say otherwise, believe the
repeats over the delta.

## Tiers

Tier the instruments by cost and authority. The probe — smaller
scale, subset, fast — answers intermediate questions and ranks
candidates; the authoritative run — full, expensive — decides what is
banked. Middle judgments ride probes; entries ride authority; a
promotion that skips the authoritative run is not efficiency, it is
unbanked hope. And a probe is only cheap when it is narrow: scope
what it touches, or the probe costs more than the measurement it
replaced.

## Hygiene

One variable per change. A delta measured across two simultaneous
changes is attributable to neither; attribution is the whole value of
a measurement, and confounded measurements are expensive noise (see
controls_and_confounders for the design side).

Report numbers as triples: value, spread, conditions. A bare number
is not comparable — to your own past measurements or anyone's.

A number that surprises belongs to critical_validation before it
belongs anywhere else.

Provenance: the OMILREC campaigns — the ±1.5% baseline spread rule,
the probe/full-tier economics, every banked win.
