# xsbench_opt — XSBench benchmark task for SimpleEvolution

Optimize the XSBench macroscopic neutron cross-section lookup kernel (a C
mini-app from Argonne, the computational kernel behind Monte Carlo transport
codes like OpenMC) for single-threaded speed while keeping its verification
checksum bit-identical.

This task follows the same shape as `examples/tiny_algo_opt`, but the target is
a compiled C benchmark: the Scientist proposes kernel/data-layout/algorithm
changes, the Executor edits only `src/`, and the Harness builds, verifies
(checksum gate), and benchmarks (lookups/s) inside the Apptainer runtime.

## Layout

- `repo/` — the optimization target. Clean XSBench baseline (`src/`), frozen
  eval scripts (`scripts/`), frozen reference checksum + baseline record
  (`benchmarks/`). **Upstream "answers" are removed**: the optimized-kernel
  variants shipped inside `Simulation.c`, the accelerator ports (`cuda/`,
  `hip/`, `opencl/`, `sycl/`, `openmp-offload/`), and the hardcoded reference
  checksums in `io.c`. The binary always prints its raw checksum; correctness
  is judged by the harness against `benchmarks/reference_hash.txt`.
- `reference/` — the **hidden human-expert reference** (NOT exposed to the
  agent): the upstream optimized kernel, measured on this machine, plus the
  published XSBench optimization literature. See `reference/RESULTS.md`.
- `task.yaml` — SimpleEvolution task config (paths relative to this file).
- `apptainer.def` — runtime image recipe (gcc/make/OpenMP + node/claude).
- `setup.sh` — one-time init: make `repo/` a git repo, optionally build the image.

## Run

```bash
# 1. One-time init (git repo + Apptainer image; image build needs Apptainer).
./examples/xsbench_opt/setup.sh

# 2. Configure the two roles in task.yaml. The proposer (`researcher`) speaks
#    the OpenAI Chat Completions protocol (api: hepai | openai | zhipu |
#    anthropic); the executor stays on the Anthropic protocol via the `claude`
#    CLI (api: anthropic). Export the matching keys, never stored in the file:
export OPENAI_API_KEY='<proposer-key>'    # researcher (openai, etc.)
export ANTHROPIC_API_KEY='<executor-key>' # executor (claude CLI -> anthropic)

# 3. Prepare the environment: git + image check + run-dir + root node.
python -m simpleevo --run-dir runs/xsbench-001 init --config examples/xsbench_opt/task.yaml

# 4. Start the evolution (paths resolve correctly because task.yaml uses
#    config-relative paths). `run` implies init if not already done.
python -m simpleevo --run-dir runs/xsbench-001 run --config examples/xsbench_opt/task.yaml

# 5. Later, continue the same run without --config (reconciles offline results).
python -m simpleevo --run-dir runs/xsbench-001 resume

# 6. Inspect the research tree.
python -m simpleevo --run-dir runs/xsbench-001 status
python -m simpleevo --run-dir runs/xsbench-001 inspect --node <node_id>
```

## Metrics contract

The Harness parses structured `KEY=VALUE` lines from eval output
(`experiment/evaluator.py`). This task emits:

| key | source | parsed as |
| --- | --- | --- |
| `VERIFY` | `scripts/check_verify.sh` exit → `echo VERIFY=PASS\|FAIL` | gate bool |
| `lookups_per_sec` | `scripts/bench.sh` → `print(f"lookups_per_sec={...}")` | objective float (higher better) |

Frozen benchmark run: `-m event -s small -G unionized -t 1 -l 2000000`
(see `repo/scripts/config.sh`). Single-threaded on purpose — the FOM measures
the algorithm's search/interpolation/memory cost, not thread scaling, and stays
reproducible on a shared machine. `VERIFY` is the only gate; among passing
changes, higher `lookups_per_sec` advances the frontier.

## Comparing against human experts

`reference/RESULTS.md` records the human-expert bar for this task: the
upstream XSBench optimized kernel (sort-based event kernel, `-k 1`) built and
measured with the identical frozen config on this machine, plus published
XSBench optimization results. A frontier node that beats the official optimized
kernel on the same hardware has genuinely surpassed the published reference
implementation.

## Trace the history

```bash
git -C runs/xsbench-001/repo log --oneline
cat runs/xsbench-001/telemetry/frontier_size.jsonl
```
