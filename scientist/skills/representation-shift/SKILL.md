---
name: representation-shift
description: >-
  The problem has been optimized in one object layer for a long time
  and gains are flattening: change what the problem is represented as,
  not how hard it is worked.
tier: discovery
audience: shared
---

# representation_shift — changing what the problem is

Load this when a problem has lived in one representation for a long
time — every improvement is now a refinement inside it. Breakthroughs
are rarely smarter moves in the old representation; they are the same
problem suddenly becoming easy in a new one.

## The shifts

Systematically try, one at a time:

- variable representation — what are the objects? (events vs stages
  vs memory layout vs instruction streams)
- objective representation — what is being driven? (per-call cost vs
  call count vs total algorithm structure)
- data representation — in what form does the input live? (tables vs
  caches vs precomputed invariants vs indices)
- algorithmic abstraction — solve, or look up? compute, or memorize?
  optimize, or surrogate and correct?
- spatial vs frequency; local vs global; continuous vs discrete;
  per-operation vs whole-process; component vs system.

After each shift, the one question that matters: is the old
bottleneck still a bottleneck in the new representation? If it
dissolves, the shift paid; if it persists, the shift is cosmetic.

## The canonical chain

"Why is each FCN evaluation slow?" → "why are there this many FCN
evaluations?" → "must the stages be organized this way at all?" →
"is this an optimization problem, or a reformulation problem?" Each
step is a representation shift, and each one moved the floor more
than any amount of work inside the previous representation.

Provenance: Co-Scientist out-of-box / evolution operators; the
OMILREC tuition, where every surviving wall fell to a
representation change, not a faster kernel.
