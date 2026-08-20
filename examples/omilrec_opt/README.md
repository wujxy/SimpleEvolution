# omilrec_opt — OMILREC benchmark task for SimpleEvolution

Optimize the OMILREC v1.0.0 reconstruction algorithm (JUNO's macroscopic
likelihood vertex/energy fitter) for single-threaded speed while keeping its
frozen correctness gates green: FCN relative LL error < 1e-13, 18-event E2E
within 4 mm / 7 keV / 10 ps / 0.1 PE, and a single-threaded benchmark.

This task follows the same shape as `examples/xsbench_opt`, but the target is
a heavy JUNO C++ project that is **not self-contained**: the eval needs the
JUNO software stack (mounted from the host's `/cvmfs`) and the benchmark input
+ reconstruction maps (`/data/juno/dingxf`). Those are bound read-only into
the eval sandbox through `task.yaml`'s `read_only_binds` (this required a
small framework change: the evaluator lane now honors `read_only_binds`, which
the proposer lane already did).

## Layout

- `repo/` — the optimization target. Clean OMILREC v1.0.0 baseline
  (`OMILRECV2/src/`), frozen gate tests (`tests/`), frozen eval scripts
  (`scripts/`), frozen v1.0.0 performance baseline (`baseline/`). No bundled
  "answer" exists: the repo ships only the v1.0.0 algorithm, and the truth
  (FCN golden points, E2E reference ROOT, perf baseline) is the gate, not an
  answer.
- `reference/` — the **hidden human-expert reference** (NOT exposed to the
  agent): the v1.0.0 baseline bar, the measured expert optimization trajectory
  (sibling `omilrec/` repo, v1.0.1 → v1.12.1), and the known-safe optimization
  catalog. See `reference/RESULTS.md`.
- `task.yaml` — SimpleEvolution task config (paths relative to this file).
- `apptainer.def` — runtime image recipe. This is the "fat" junosw image: beyond
  the base harness tools it ships the system-library chain JUNO's shared libs
  link but almalinux:9 does not (freetype, X11 client libs, libGLU, libicu,
  libnsl2, time) plus pytest staged for the CVMFS Python 3.11. Proven in
  SimpleLoop-v0.2.1's `junosw-apptainer.def`; the SIF itself is gitignored
  (`*.sif`) and built by `setup.sh` from this def. To reuse the prebuilt image
  without rebuilding, copy the one from `SimpleLoop-v0.2.1/examples/`.
- `setup.sh` — one-time init: check host prerequisites (/cvmfs, /data), make
  `repo/` a git repo, build the image if `apptainer.sif` is absent.

## Run

```bash
# 1. One-time init (host prerequisites + git repo + Apptainer image).
./examples/omilrec_opt/setup.sh

# 2. Configure the two roles in task.yaml. The proposer (`researcher`) speaks
#    the OpenAI Chat Completions protocol (api: hepai | openai | zhipu |
#    anthropic); the executor stays on the Anthropic protocol via the `claude`
#    CLI (api: anthropic). Export the matching keys, never stored in the file:
export OPENAI_API_KEY='<proposer-key>'    # researcher (openai, etc.)
export ANTHROPIC_API_KEY='<executor-key>' # executor (claude CLI -> anthropic)

# 3. Prepare the environment: git + image check + run-dir + root node.
python -m simpleevo --run-dir runs/omilrec-001 init --config examples/omilrec_opt/task.yaml

# 4. Start the evolution (paths resolve correctly because task.yaml uses
#    config-relative paths). `run` implies init if not already done.
python -m simpleevo --run-dir runs/omilrec-001 run --config examples/omilrec_opt/task.yaml

# 5. Later, continue the same run without --config (reconciles offline results).
python -m simpleevo --run-dir runs/omilrec-001 resume

# 6. Inspect the research tree.
python -m simpleevo --run-dir runs/omilrec-001 status
python -m simpleevo --run-dir runs/omilrec-001 inspect --node <node_id>
```

## Metrics contract

The Harness parses structured `KEY=VALUE` lines from eval output
(`experiment/evaluator.py`). `scripts/sl_eval_v100.sh --evtmax 100` emits:

| key | parsed as |
| --- | --- |
| `SPEED_MS` | objective float (ms/evt, **lower better**) |
| `CONTRACT` | gate bool — static gate contract passes |
| `FCN` | gate bool — relative LL error < 1e-13 |
| `CONSISTENCY` | gate bool — 18-event E2E within limits |
| `SINGLE_THREADED` | gate bool — OMP/MKL/ROOT pinned to 1 thread |

A change is accepted only if all four gates are `PASS`; among accepted
changes, lower `SPEED_MS` advances the frontier. The eval is single-threaded
by construction — speedups must come from the algorithm, not parallelism.

**Cost**: each eval is heavy — two cmake builds (probe ON then OFF), the FCN
(~40 s) and E2E (~50 s) gate suites, and a 100-event benchmark (~90 s) ⇒
**~5-8 minutes per candidate** (`eval_timeout_seconds: 1800`).

## Comparing against human experts

`reference/RESULTS.md` records the human-expert bar: the frozen v1.0.0
baseline (`~920 ms/evt`, median of 3×100-event single-thread runs on this
machine) and the measured expert optimization trajectory in the sibling
`omilrec/` repo (v1.0.1 → v1.12.1). A frontier node that beats the baseline
while passing the v1.0.0 gates reproduces what the published expert
optimizations achieved; beating the best v1.0.0-compatible version surpasses
them.

## Host prerequisites

The eval is **not self-contained** — it needs (see `setup.sh`):
- `/cvmfs/juno.ihep.ac.cn/...` — JUNO software stack (J26.1.1) mounted on the
  host;
- `/data/juno/dingxf/inputs/index_12628_rtraw_1.json` and
  `/data/juno/dingxf/OMILREC_maps` — benchmark input and reconstruction maps
  (SHA-256 pinned in `repo/baseline/manifest.json`).

`task.yaml` binds both into the sandbox read-only; if your data lives
elsewhere, override `OMILRECV2_TEST_INPUT` / `OMILRECV2_TEST_RECMAP` in the
eval environment and update `read_only_binds` to match.

## Trace the history

```bash
git -C runs/omilrec-001/repo log --oneline
cat runs/omilrec-001/telemetry/frontier_size.jsonl
```
