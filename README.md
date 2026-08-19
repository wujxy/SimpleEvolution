# SimpleEvolution

Event-driven Research Tree evolution: **Node → Proposer → Proposal →
Experiment → New Node**. A Scientist thread proposes changes to a research
target; an Executor implements them; the Harness measures, gates, and advances
a GEPA-style per-axis frontier.

Design source of truth: [docs/simpleevolution_design.md](docs/simpleevolution_design.md).
Implementation notes: [docs/simpleevolution_implementation.md](docs/simpleevolution_implementation.md).

## Install

```bash
pip install -e .            # provides the `simpleevo` command
# or run without installing:
python -m simpleevo --help
```

## Commands

```bash
# 1. Prepare the environment: git repo + image check + run-dir + root node.
simpleevo --run-dir runs/tiny-001 init --config examples/tiny_algo_opt/task.yaml

# 2. Start the evolution (init is implied if not already done).
simpleevo --run-dir runs/tiny-001 run --config examples/tiny_algo_opt/task.yaml

# 3. Continue an existing run (loads runs/<id>/task.yaml; reconciles offline results).
simpleevo --run-dir runs/tiny-001 resume

# Inspect state.
simpleevo --run-dir runs/tiny-001 status
simpleevo --run-dir runs/tiny-001 inspect --node <node_id>
simpleevo --run-dir runs/tiny-001 reseed --node <node_id>
```

Worked examples:

- [examples/tiny_algo_opt/](examples/tiny_algo_opt/) — pure-Python pair-counting
  speed task (`tinyalgo`).
- [examples/xsbench_opt/](examples/xsbench_opt/) — compiled-C benchmark: optimize
  the XSBench macroscopic cross-section lookup kernel for single-threaded
  lookups/s while keeping its verification checksum bit-identical. Ships a
  hidden human-expert reference for comparison (`reference/`).
