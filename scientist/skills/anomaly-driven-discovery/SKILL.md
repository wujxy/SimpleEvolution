---
name: anomaly-driven-discovery
description: >-
  A residual, an outlier, a persistent misfit, or a failure that
  contradicts the current model: an anomaly is the entry point of a
  new mechanism, not noise to be cleaned.
tier: discovery
audience: shared
---

# anomaly_driven_discovery — mining the misfit

Load this when something refuses to fit: a residual, an outlier, a
subset of the data the model never explains, predictions right in
general but systematically wrong in one region, or a failure result
that contradicts what the model says should happen. The reflex to
explain anomalies away is exactly backwards — models are extended by
what they cannot explain.

## The protocol

Before treating it as noise, ask in order:

1. Stability — does the anomaly reproduce, or is it a one-off of the
   instrument?
2. Dependence — on which variables does it appear and disappear?
   The dependence pattern is already data about the mechanism.
3. Attribution — which existing assumption, if wrong, would produce
   exactly this pattern? An anomaly is an assumption-audit entry
   that volunteered itself.
4. The minimal core — what is the smallest part the current model
   genuinely cannot account for? Work that, not the whole residual.
5. The covering mechanism — can one new mechanism explain the normal
   region AND the anomaly? A mechanism that covers only the anomaly
   is an epicycle; one that covers both is a discovery.
6. The fork — what observation distinguishes artifact from
   phenomenon? (cheapest-discriminating-experiment takes it from
   there.)

## The disposition

Most anomalies are artifacts — which is why the protocol starts with
stability and ends with a discriminating observation, not with
belief. But the ones that survive are where new mechanisms come from,
and a team with no anomaly channel has no discovery channel.

Provenance: the generative-AI weakness for analogical increment over
anomaly-driven reframing (literature); proposer arbitration
2026-09-02.
