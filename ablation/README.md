# SimpleEvolution ablation framework

Ablation of SimpleEvolution's two structural knobs on a task:

| arm | researcher | frontier width | what it isolates |
| --- | --- | --- | --- |
| `coding-agent` | no-op "keep going" proposer | k=1 (chain) | a single self-directed coding agent, no research separation |
| `loop` | real researcher | k=1 (chain) | value of researcher/executor separation (serial loop) |
| `topk` | real researcher | k=3 (tree) | value of frontier breadth (parallel lineages) |

Every arm runs the **same** scheduler, executor, eval, gates, commit path and
telemetry; the arms differ only in `frontier_top_k` and in whether the
researcher slot is the real proposer or a trivial one. `proposal_slots=1`,
`generator_reseed=false` and `max_research_per_node=100` for all arms, so
frontier breadth is the single ablation variable (the shipped default config
sweeps those knobs separately).

Every arm writes a standard SimpleEvolution run-dir (`runs/<root>/<arm>/seed-N`),
so the reporting layer and the ablation plotter are shared unchanged.

## Commands

```bash
# One arm instance, stopped at 10 terminal evals or $4 of LLM spend.
python -m ablation.driver run \
  --config examples/xsbench_opt/task.yaml \
  --arm topk --run-dir runs/ablation/topk/seed-1 \
  --seed 1 --max-evals 10 --budget-usd 4.0

# Formal experiment: all arms x 3 seeds, $4/arm budget, low-effort config.
# --max-evals 20 so the BUDGET is the binding cap (chain test showed a 10-eval
# cap stops loop ~$2.3 / topk ~$1.7, well before $4).  Each run is its own
# subprocess + run-dir; per-seed keys cycle when --openai-keys / --anthropic-keys
# are given (comma list), otherwise every seed shares the ambient key.
python -m ablation.driver all \
  --config examples/xsbench_opt/task.yaml \
  --runs-root runs/ablation --seeds 3 \
  --max-evals 20 --budget-usd 4.0 \
  --openai-keys k1,k2,k3 --anthropic-keys k1,k2,k3

# The figure: budget (cumulative USD) on x, lookups/s vs baseline on y,
# median +/- min/max band per arm.
python -m ablation.driver plot --runs-root runs/ablation --out ablation.png
```

Per-seed API keys: `--openai-keys k1,k2,k3` / `--anthropic-keys k1,k2,k3`
(cycled across seeds). Unset → the ambient `OPENAI_API_KEY` /
`ANTHROPIC_AUTH_TOKEN` is reused for every seed.

## Budget accounting

x-axis cost comes from `telemetry/usage.jsonl` replayed at the config's
DeepSeek-flash prices (same code path as `simpleevo/reporting/data.py`). For
the `coding-agent` arm the proposer spends nothing, so its cost is executor-only;
`loop`/`topk` also pay the researcher. Equal budget therefore buys `coding-agent`
more evals — whether that pays off in performance is what the figure answers.

The eval cap counts *terminal* experiments (completed / gate-rejected /
no-change); an experiment already in flight when the cap trips is drained, so a
run lands on `max_evals` or `max_evals + 1`.
