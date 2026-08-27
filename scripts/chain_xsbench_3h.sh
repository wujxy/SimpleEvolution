#!/usr/bin/env bash
# Sequential 3h+3h chain: scientist arm -> scientist gate replay ->
# coding-agent arm (v5 machinery, incl. its internal snapshot replay).
# Everything strictly serial so bench pin 9 never sees interference.
#
#   setsid nohup bash scripts/chain_xsbench_3h.sh >> runs/xsbench-3h/chain.log 2>&1 &
#
# Chain marker: runs/xsbench-3h/CHAIN_DONE.
set -euo pipefail
cd /datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution

ROOT=runs/xsbench-3h
SCI=$ROOT/scientist
CA=$ROOT/coding-agent
PY=/datafs/users/wujxy/py_venv/my_env/bin/python
MARKER=$ROOT/CHAIN_DONE

if [ -e "$MARKER" ]; then
    echo "[chain] already done ($MARKER present)"; exit 0
fi
mkdir -p "$ROOT"

if [ -e "$SCI/world/.scientist/conclusion.json" ]; then
    echo "[chain] $(date) resume: scientist already concluded — skip 1-2"
elif [ -s "$SCI/replay.csv" ]; then
    echo "[chain] $(date) resume: replay done — skip 1-3"
else
    echo "[chain] $(date) phase 1/4: scientist arm (probe + 3h session)"
    bash scripts/run_xsbench_3h_scientist.sh "$SCI"

    echo "[chain] $(date) phase 2/4: waiting for the scientist's exit contract"
    DEADLINE=$(( $(date +%s) + 10800 + 1800 ))   # wall cap + checkpoint margin
    until [ -e "$SCI/world/.scientist/conclusion.json" ] || \
          [ "$(date +%s)" -ge "$DEADLINE" ]; do
        sleep 60
    done
    if [ ! -e "$SCI/world/.scientist/conclusion.json" ]; then
        echo "[chain] WARNING: no conclusion.json at deadline — replaying " \
              "whatever snapshots exist" >&2
    fi
    sleep 120   # sidecar's final snapshot + notebook checkpoint settle
fi

if [ -s "$SCI/replay.csv" ]; then
    echo "[chain] replay.csv already present — skip phase 3"
else
    echo "[chain] $(date) phase 3/4: scientist gate replay (idle machine, pin 9)"
    $PY scripts/replay_xsbench.py \
        --snapshots "$SCI/snapshots" --out "$SCI/replay.csv" --pin 9
fi
echo "[chain] scientist replay: $(wc -l < "$SCI/replay.csv") csv rows"

echo "[chain] $(date) phase 4/4: coding-agent arm (3h + internal replay)"
unset APPTAINER_BIND
set -a
eval "$($PY -c 'import json,os;d=json.load(open(os.path.expanduser("~/.claude/settings_ds.json.backup")))["env"];[print(f"export {k}=\x27{v}\x27") for k,v in d.items()]')"
set +a
$PY scripts/run_cont_agent.py \
    --config examples/xsbench_opt/task.yaml \
    --run-dir "$CA" \
    --max-seconds 10800 \
    --budget-usd 30 \
    --snapshot-every 300 \
    --live-every 0 \
    --agent-core 9 \
    --live-core 11 \
    >> "$ROOT/coding-agent.run.log" 2>&1

date > "$MARKER"
echo "[chain] $(date) CHAIN DONE: $SCI/replay.csv + $CA (simpleevo.db)"
