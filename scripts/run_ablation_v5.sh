#!/usr/bin/env bash
# Ablation-v5: three-arm XSBench comparison under a 4h wall-clock cap.
#
#   coding-agent  ablation.driver, no-op proposer, k=1 chain   (BENCH_PIN=9)
#   loop          ablation.driver, real researcher, k=1 chain  (BENCH_PIN=10)
#   tree          run_supervisor_test.py, Supervisor sole gate (BENCH_PIN=11,
#                 concurrent evals lease exclusive cores 11..16 in the pool)
#
# Eval/budget caps are set far out of reach (999 evals / $1000): TIME is the
# single binding constraint.  At 4h each driver stops allocating and drains
# its last in-flight eval, then prints "] done: ".  The detached plot daemon
# renders the time-axis figure hourly and the final time+cost pair.
#
# Everything is setsid-detached so a session restart cannot kill the run;
# logs append under runs/ablation-v5/ (rerunning the same command restarts
# the drivers, which reconcile and continue under the same limits).
set -euo pipefail
cd /datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution

PY=/datafs/users/wujxy/py_venv/my_env/bin/python
RUNS_ROOT=runs/ablation-v5
MAX_SECONDS=14400   # 4h wall-clock cap, the binding constraint
MAX_EVALS=999       # effectively unlimited
BUDGET_USD=1000     # effectively unlimited

if [ -e "$RUNS_ROOT/t0" ]; then
  echo "refusing to start: $RUNS_ROOT/t0 exists (previous v5 launch?)" >&2
  echo "move/clear $RUNS_ROOT first if you really want a fresh run" >&2
  exit 1
fi

# DeepSeek credentials (5 ANTHROPIC_* vars) — without this the executor
# channel 401s inside every experiment (token must match the task config's
# deepseek base_url; see scripts/run_supervisor_test.py's preflight note).
while IFS='=' read -r k v; do export "$k=$v"; done < \
  <(jq -r '.env|to_entries[]|"\(.key)=\(.value)"' "$HOME/.claude/settings_ds.json.backup")
unset APPTAINER_BIND

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "warning: OPENAI_API_KEY not set — researcher channel will fail" >&2
fi

mkdir -p "$RUNS_ROOT"
date +%s > "$RUNS_ROOT/t0"

setsid nohup env BENCH_PIN=9 "$PY" -m ablation.driver run \
  --config examples/xsbench_opt/task.yaml --arm coding-agent \
  --run-dir "$RUNS_ROOT/coding-agent/seed-1" --seed 1 \
  --max-evals "$MAX_EVALS" --budget-usd "$BUDGET_USD" --max-seconds "$MAX_SECONDS" \
  >> "$RUNS_ROOT/coding-agent.run.log" 2>&1 < /dev/null &

setsid nohup env BENCH_PIN=10 "$PY" -m ablation.driver run \
  --config examples/xsbench_opt/task.yaml --arm loop \
  --run-dir "$RUNS_ROOT/loop/seed-1" --seed 1 \
  --max-evals "$MAX_EVALS" --budget-usd "$BUDGET_USD" --max-seconds "$MAX_SECONDS" \
  >> "$RUNS_ROOT/loop.run.log" 2>&1 < /dev/null &

setsid nohup env BENCH_PIN=11 "$PY" scripts/run_supervisor_test.py \
  --config examples/xsbench_opt/task-supervisor-branch.yaml \
  --run-dir "$RUNS_ROOT/tree/seed-1" --seed 1 \
  --max-evals "$MAX_EVALS" --budget-usd "$BUDGET_USD" --max-seconds "$MAX_SECONDS" \
  >> "$RUNS_ROOT/tree.run.log" 2>&1 < /dev/null &

setsid nohup bash scripts/plot_ablation_v5_hourly.sh "$RUNS_ROOT" ablation-v5 \
  >> "$RUNS_ROOT/plot.log" 2>&1 < /dev/null &

sleep 2
echo "launched (logs under $RUNS_ROOT/):"
ps -u "$USER" -o pid,pgid,etime,args \
  | grep -E "ablation.driver run|run_supervisor_test|plot_ablation_v5" | grep -v grep || true
