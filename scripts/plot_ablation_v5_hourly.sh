#!/usr/bin/env bash
# Ablation-v5 plot daemon: render the three-arm figure every hour while the
# 4h comparison runs, then a final render (time + cost axes) once every arm's
# driver has printed its "done:" line — or at a hard failsafe deadline, so a
# crashed driver never wedge the final figure.
#
# Launched detached by scripts/run_ablation_v5.sh; survives session restarts.
set -u
cd /datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution
PY=/datafs/users/wujxy/py_venv/my_env/bin/python
RUNS_ROOT=${1:-runs/ablation-v5}
PREFIX=${2:-ablation-v5}
T0=$(cat "$RUNS_ROOT/t0")
ARMS=(coding-agent loop tree)

render() { # render <x-axis> <out.png>
  "$PY" -m ablation.driver plot --runs-root "$RUNS_ROOT" --x-axis "$1" --out "$2" || true
}

# Hourly interim figures on the time axis (the binding constraint of v5).
for h in 1 2 3; do
  target=$((T0 + h * 3600))
  now=$(date +%s)
  if (( now < target )); then sleep $((target - now)); fi
  echo "[$(date '+%F %T')] interim h$h"
  render time "${PREFIX}-interim-h${h}.png"
done

# Final render once all three drivers are done ("] done: " terminal line);
# failsafe at T0+6.5h (4h cap + worst-case 1h drain + 1.5h slack).
deadline=$((T0 + 23400))
while :; do
  alldone=1
  for a in "${ARMS[@]}"; do
    grep -q "] done: " "$RUNS_ROOT/$a.run.log" || alldone=0
  done
  if (( alldone )); then break; fi
  now=$(date +%s)
  if (( now >= deadline )); then
    echo "[$(date '+%F %T')] WARNING: failsafe deadline hit before all arms done"
    break
  fi
  sleep 60
done
echo "[$(date '+%F %T')] final render"
render time "${PREFIX}.png"
render cost "${PREFIX}-cost.png"
echo "[$(date '+%F %T')] plot daemon complete"
