#!/usr/bin/env bash
# singlenode — scientist mode: the ENTIRE scientist run executes inside
# ONE apptainer container (mounts are the boundary; the host only opens
# the world and collects it). See node_common.sh for mount semantics.
#
# Usage:   bash singlenode/run_scientist.sh [RUN_DIR]
# Env:     NODE_IMAGE / NODE_TEMPLATE / SPEC_TEMPLATE (defaults: xsbench)
#          BENCH_PIN (optional core pin), FORCE=1, SMOKE_ONLY=1, WALL=seconds
# After:   python scripts/replay_xsbench.py \
#              --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
set -euo pipefail
source "$(dirname "$0")/node_common.sh"
cd "$REPO_ROOT"

RUN_DIR=${1:-runs/singlenode/scientist-$(date +%m%d-%H%M)}
WALL=${WALL:-10800}
PY=${PY:-/datafs/users/wujxy/py_venv/my_env/bin/python}

node_unset_inherited_binds

if [ -e "$RUN_DIR/run.log" ] || [ -d "$RUN_DIR/world" ]; then
    if [ "${FORCE:-0}" != "1" ]; then
        echo "refusing to overwrite existing run at $RUN_DIR (FORCE=1 to override)" >&2
        exit 1
    fi
    rm -rf "$RUN_DIR/world" "$RUN_DIR/snapshots" "$RUN_DIR/run.log" "$RUN_DIR/pkg"
fi

# ds runtime env for the assistant claude subprocesses (credentials —
# never echoed; baked into the spec below).
set -a
eval "$($PY -c 'import json,os;d=json.load(open(os.path.expanduser("~/.claude/settings_ds.json.backup")))["env"];[print(f"export {k}=\x27{v}\x27") for k,v in d.items()]')"
set +a

node_prepare_run_dir
BASE_SHA=$(git -C "$RUN_DIR/world" rev-parse HEAD)

# spec with live credentials (default recipe; SPEC_TEMPLATE overridable)
$PY - "$SPEC_TEMPLATE" "$RUN_DIR/spec.json" "$BASE_SHA" <<'EOF'
import json, os, sys
spec = json.load(open(sys.argv[1]))
tide = json.load(open("runs/tide-demo-1/spec.json"))
ds = json.load(open(os.path.expanduser(
    "~/.claude/settings_ds.json.backup")))["env"]
spec["base_sha"] = sys.argv[3]
if spec.get("model", {}).get("api_key") in (None, "", "FILL_BEFORE_RUNNING"):
    spec["model"]["api_key"] = tide["model"]["api_key"]
env = spec.setdefault("assistant", {}).setdefault("env", {})
for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
    if env.get(key) in (None, "", "FILL_BEFORE_RUNNING"):
        env[key] = ds[key]
open(sys.argv[2], "w").write(
    json.dumps(spec, indent=2, ensure_ascii=False))
EOF
chmod 600 "$RUN_DIR/spec.json"

# freeze the scientist package for this run (upgrade = re-copy)
cp -a "$REPO_ROOT/scientist" "$RUN_DIR/pkg/scientist"

node_scientist_env

# smoke gate (fail-closed), then optional stop
bash "$SINGLENODE_DIR/smoke.sh" "$RUN_DIR" "$BASE_SHA"
if [ "${SMOKE_ONLY:-0}" = "1" ]; then
    echo "SMOKE_ONLY=1 — stopping after smoke. run_dir=$RUN_DIR"
    exit 0
fi

# pre-flight probe: one model call through the real mounted world
node_container python3 -m scientist.cli \
    --spec /spec.json --world /work --repo /repo --scratch /scratch --probe

# detached run (scientist_container is a shell FUNCTION — the detached
# call re-sources node_common.sh in a bash -c; nohup cannot exec functions)
export RUN_DIR
setsid nohup bash -c \
    'source "$0/node_common.sh"; node_container python3 -m scientist.cli --spec /spec.json --world /work --repo /repo --scratch /scratch' \
    "$SINGLENODE_DIR" < /dev/null >> "$RUN_DIR/run.log" 2>&1 &
AGENT=$!
setsid nohup "$PY" "$REPO_ROOT/scripts/snapshot_world_loop.py" \
    --world "$RUN_DIR/world" --out "$RUN_DIR/snapshots" \
    --every 60 --max-seconds "$WALL" \
    >> "$RUN_DIR/snapshot.log" 2>&1 &
SNAP=$!
echo "run_dir=$RUN_DIR agent_pid=$AGENT snapshot_pid=$SNAP wall=${WALL}s base_sha=$BASE_SHA"
echo "stop with: kill -TERM -- -$AGENT"
echo "tail: tail -f $RUN_DIR/run.log"
