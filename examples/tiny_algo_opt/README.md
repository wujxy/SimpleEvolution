# tiny_algo_opt — SimpleEvolution example

A self-contained optimization task for SimpleEvolution. The Scientist
(proposer) proposes experiments that speed up `tinyalgo.count_pairs`; the
Executor edits only the `tinyalgo/` package; the Harness runs correctness,
drift, and benchmark commands. Only changes passing the `CORRECTNESS` and
`DRIFT` gates advance to the `ms_per_call` objective frontier.

This example is a port of `SimpleLoop/examples/tiny_algo_opt` to the
SimpleEvolution task config schema (`simpleevo/config.py`).

## Layout

- `repo/` — the optimization target (pure-Python package + tests + scripts)
- `task.yaml` — SimpleEvolution task config (paths relative to this file)
- `apptainer.def` — runtime image recipe (bash/git/python + node/claude)
- `setup.sh` — one-time init: make `repo/` a git repo, optionally build the image

## Run

```bash
# 1. One-time init (git repo + Apptainer image; image build needs Apptainer).
./examples/tiny_algo_opt/setup.sh

# 2. Configure the two roles in task.yaml. The proposer (`researcher`) speaks
#    the OpenAI Chat Completions protocol (api: hepai | openai | zhipu |
#    anthropic); the executor stays on the Anthropic protocol via the `claude`
#    CLI (api: anthropic). Export the matching keys, never stored in the file:
export OPENAI_API_KEY='<proposer-key>'    # researcher (openai, etc.)
export ANTHROPIC_API_KEY='<executor-key>' # executor (claude CLI -> anthropic)

# 3. Prepare the environment: git + image check + run-dir + root node.
python -m simpleevo --run-dir runs/tiny-001 init --config examples/tiny_algo_opt/task.yaml

# 4. Start the evolution (paths resolve correctly because task.yaml uses
#    config-relative paths). `run` implies init if not already done.
python -m simpleevo --run-dir runs/tiny-001 run --config examples/tiny_algo_opt/task.yaml

# 5. Later, continue the same run without --config (reconciles offline results).
python -m simpleevo --run-dir runs/tiny-001 resume

# 6. Inspect the research tree.
python -m simpleevo --run-dir runs/tiny-001 status
python -m simpleevo --run-dir runs/tiny-001 inspect --node <node_id>
```

## Metrics contract

The Harness parses structured `KEY=VALUE` lines from eval output
(`experiment/evaluator.py`). This example emits:

| key | source | parsed as |
| --- | --- | --- |
| `CORRECTNESS` | `pytest` exit → `echo CORRECTNESS=PASS\|FAIL` | gate bool |
| `DRIFT` | `check_drift.py` exit → `echo DRIFT=PASS\|FAIL` | gate bool |
| `ms_per_call` | `bench.py` → `print(f"ms_per_call={ms:.4f}")` | objective float |

## Trace the history

```bash
git -C runs/tiny-001/repo log --oneline
cat runs/tiny-001/telemetry/frontier_size.jsonl
```
