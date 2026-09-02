---
name: cross-domain-analogy
description: >-
  This structure has been solved somewhere else, under different
  names: abstract the problem, find the structural twin, and port
  the mechanism through an explicit mapping.
tier: discovery
audience: shared
---

# cross_domain_analogy — the structural twin

Load this when the local repertoire is exhausted but the structure of
the problem feels familiar, or when a distant field is known to live
with the same shape of difficulty. Analogy fails when it ports
vocabulary; it pays when it ports mechanism — and the difference is
the mapping.

## The protocol

1. Abstract the current problem first: what are the objects? the
   constraints? the input-output relation? the bottleneck? what is
   being estimated, searched, or optimized?
2. Send the abstraction hunting (searchers in parallel, different
   fields each): where else does this structure appear — under what
   names?
3. Write the mapping explicitly, row by row:

   the other domain        this problem
   state                ↔  reconstruction parameters
   likelihood search    ↔  the optimization loop
   cached surrogate     ↔  (what plays this role here?)

4. The mapping gates the transfer: every load-bearing row must have a
   counterpart before anything moves. A missing row kills the analogy
   cheaply — that is the protocol working, not failing.
5. Port the mechanism, not the solution: what transfers is the trick
   that made the twin problem tractable, re-derived in local terms.

A successful analogy changes the question rather than refining the
answer — if the ported mechanism only speeds up the old question, it
was an optimization, not an analogy.

Provenance: Co-Scientist's analogies / inspiration-from-hypotheses
operators; analogical_transfer practice (merged here 2026-09-02).
