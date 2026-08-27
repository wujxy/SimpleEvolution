#!/usr/bin/env bash
# 3h standalone scientist arm on XSBench — P0 budget spec
# (examples/xsbench_opt/spec.json: 10800s wall, 400 steps, 900s bash,
# 45min work jobs). Detached (setsid + nohup): survives session restarts.
#
# Usage:  bash scripts/run_xsbench_3h_scientist.sh [RUN_DIR]
# After:  python scripts/replay_xsbench.py \
#             --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
set -euo pipefail
cd /datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution

RUN_DIR=${1:-runs/xsbench-3h/scientist}
WALL=10800
PY=/datafs/users/wujxy/py_venv/my_env/bin/python

# Never clobber a live/finished run without an explicit override.
if [ -e "$RUN_DIR/run.log" ] || [ -d "$RUN_DIR/world" ]; then
    if [ "${FORCE:-0}" != "1" ]; then
        echo "refusing to overwrite existing run at $RUN_DIR (FORCE=1 to override)" >&2
        exit 1
    fi
    rm -rf "$RUN_DIR/world" "$RUN_DIR/snapshots" "$RUN_DIR/run.log"
fi

# ds runtime env for the assistant claude subprocesses (credentials —
# never echoed). Token/base_url also baked into the spec below.
unset APPTAINER_BIND
set -a
eval "$($PY -c 'import json,os;d=json.load(open(os.path.expanduser("~/.claude/settings_ds.json.backup")))["env"];[print(f"export {k}=\x27{v}\x27") for k,v in d.items()]')"
set +a

mkdir -p "$RUN_DIR"

# 1) fresh world at the frozen base sha (git history readable)
cp -a examples/xsbench_opt/repo "$RUN_DIR/world"

# 2) spec with live credentials: deepseek key from the last working
#    oneworld run; claude token/base_url from the ds settings backup
$PY - "$RUN_DIR/spec.json" <<'EOF'
import json, os, sys
spec = json.load(open("examples/xsbench_opt/spec.json"))
tide = json.load(open("runs/tide-demo-1/spec.json"))
ds = json.load(open(os.path.expanduser(
    "~/.claude/settings_ds.json.backup")))["env"]
spec["model"]["api_key"] = tide["model"]["api_key"]
spec["assistant"]["env"]["ANTHROPIC_AUTH_TOKEN"] = ds["ANTHROPIC_AUTH_TOKEN"]
spec["assistant"]["env"]["ANTHROPIC_BASE_URL"] = ds["ANTHROPIC_BASE_URL"]
open(sys.argv[1], "w").write(
    json.dumps(spec, indent=2, ensure_ascii=False))
EOF
chmod 600 "$RUN_DIR/spec.json"

# 3) pre-flight probe: one model call through the assembled context
$PY -m scientist.cli --spec "$RUN_DIR/spec.json" \
    --world "$RUN_DIR/world" --probe

# 4) the scientist (detached) + read-only snapshot sidecar (detached)
setsid nohup $PY -m scientist.cli \
    --spec "$RUN_DIR/spec.json" --world "$RUN_DIR/world" \
    >> "$RUN_DIR/run.log" 2>&1 &
AGENT=$!
setsid nohup $PY scripts/snapshot_world_loop.py \
    --world "$RUN_DIR/world" --out "$RUN_DIR/snapshots" \
    --every 60 --max-seconds "$WALL" \
    >> "$RUN_DIR/snapshot.log" 2>&1 &
SNAP=$!
echo "run_dir=$RUN_DIR agent_pid=$AGENT snapshot_pid=$SNAP wall=${WALL}s"
echo "tail: tail -f $RUN_DIR/run.log"
