"""Ablation framework for SimpleEvolution.

Three arms share every mechanism (executor, eval, gates, commit path,
telemetry) and differ only in the two structural knobs being ablated:

  coding-agent  — no researcher: a trivial "keep going" proposer lets a single
                  autonomous executor agent do research + implementation.
  loop          — top-k frontier with k=1: one lineage, serial loop.
  topk          — top-k frontier with k=3: parallel lineage tree evolution.

Every arm writes a standard SimpleEvolution run-dir (same DB schema, same
``telemetry/usage.jsonl``), so reporting and the ablation plotter are shared
across arms without special-casing.
"""
