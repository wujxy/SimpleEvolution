#!/usr/bin/env bash
# singlenode — coding-agent mode: ONE claude session inside ONE apptainer
# container (the coding-arm recipe with the simpleevo machinery removed:
# prompt on stdin, stream-json on stdout, the wall kills the group).
# Same mount semantics as the scientist mode — cross-mode comparability
# starts at the filesystem.
#
# Usage:   bash singlenode/run_coding.sh [RUN_DIR]
# Env:     TASK_FILE (prompt text; default singlenode/specs/xsbench_coding_task.txt)
#          NODE_IMAGE / NODE_TEMPLATE, WALL=seconds (default 10800),
#          FORCE=1, SMOKE_ONLY=1, BENCH_PIN
# After:   python scripts/replay_xsbench.py \
#              --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
set -euo pipefail
source "$(dirname "$0")/node_common.sh"
cd "$REPO_ROOT"

RUN_DIR=${1:-runs/singlenode/coding-$(date +%m%d-%H%M)}
WALL=${WALL:-10800}
TASK_FILE=${TASK_FILE:-$SINGLENODE_DIR/specs/xsbench_coding_task.txt}
PY=${PY:-/datafs/users/wujxy/py_venv/my_env/bin/python}

node_unset_inherited_binds

if [ -e "$RUN_DIR/trace.jsonl" ] || [ -d "$RUN_DIR/world" ]; then
    if [ "${FORCE:-0}" != "1" ]; then
        echo "refusing to overwrite existing run at $RUN_DIR (FORCE=1 to override)" >&2
        exit 1
    fi
    rm -rf "$RUN_DIR/world" "$RUN_DIR/snapshots" "$RUN_DIR/trace.jsonl" "$RUN_DIR/pkg"
fi

# credentials for the session (claude is the main process here)
set -a
eval "$($PY -c 'import json,os;d=json.load(open(os.path.expanduser("~/.claude/settings_ds.json.backup")))["env"];[print(f"export {k}=\x27{v}\x27") for k,v in d.items()]')"
set +a

node_prepare_run_dir
BASE_SHA=$(git -C "$RUN_DIR/world" rev-parse HEAD)

# spec for this mode is the goal/gate record (and the smoke's S5 uses it
# for a model call); the assistant block carries the claude credentials
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

node_coding_env

bash "$SINGLENODE_DIR/smoke.sh" "$RUN_DIR" "$BASE_SHA"
if [ "${SMOKE_ONLY:-0}" = "1" ]; then
    echo "SMOKE_ONLY=1 — stopping after smoke. run_dir=$RUN_DIR"
    exit 0
fi

# detached session: the whole claude session is ONE apptainer exec; a
# watchdog inside the detached group TERM-kills the process group at the
# wall (bash + apptainer + claude share the pgid).
export RUN_DIR TASK_FILE WALL
setsid nohup bash -c '
    source "$0/node_common.sh"
    ( sleep "${WALL}s"; kill -TERM -- -$$ 2>/dev/null ) &
    node_container claude -p --input-format text --output-format stream-json \
        --verbose --allowedTools Read,Grep,Glob,Edit,Write,Bash,WebSearch,WebFetch \
        < "$TASK_FILE" > "$RUN_DIR/trace.jsonl" 2>&1
    ' "$SINGLENODE_DIR" < /dev/null >> "$RUN_DIR/coding.log" 2>&1 &
AGENT=$!
setsid nohup "$PY" "$REPO_ROOT/scripts/snapshot_world_loop.py" \
    --world "$RUN_DIR/world" --out "$RUN_DIR/snapshots" \
    --subdir "${SNAPSHOT_SUBDIR:-src}" \
    --every 60 --max-seconds "$WALL" \
    >> "$RUN_DIR/snapshot.log" 2>&1 &
SNAP=$!
echo "run_dir=$RUN_DIR agent_pid=$AGENT snapshot_pid=$SNAP wall=${WALL}s base_sha=$BASE_SHA"
echo "stop with: kill -TERM -- -$AGENT"
echo "trace: tail -f $RUN_DIR/trace.jsonl"
