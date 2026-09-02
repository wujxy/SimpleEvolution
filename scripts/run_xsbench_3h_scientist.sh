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
# wall override: one knob drives the spec budget AND the snapshot sidecar
WALL=${WALL:-10800}
PY=/datafs/users/wujxy/py_venv/my_env/bin/python

# Never clobber a live/finished run without an explicit override.
if [ -e "$RUN_DIR/run.log" ] || [ -d "$RUN_DIR/world" ]; then
    if [ "${FORCE:-0}" != "1" ]; then
        echo "refusing to overwrite existing run at $RUN_DIR (FORCE=1 to override)" >&2
        exit 1
    fi
    rm -rf "$RUN_DIR/world" "$RUN_DIR/snapshots" "$RUN_DIR/run.log"
fi

unset APPTAINER_BIND

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
goal_file = os.environ.get("GOAL_FILE")
if goal_file:
    spec["goal"] = open(goal_file).read().strip()
spec["budget"]["wall_seconds"] = int(os.environ.get("WALL", "10800"))
spec["episode_id"] = os.environ.get("EPISODE", spec["episode_id"])
spec["assistant"]["env"]["ANTHROPIC_AUTH_TOKEN"] = ds["ANTHROPIC_AUTH_TOKEN"]
# worker model/effort: no bash-side copy — the spec's declared
# model.model / reasoning_effort govern both PI and seats
# (AssistantConfig.from_spec inherits; an explicit assistant.model is
# the one legal override, declared in the config itself)
spec["assistant"]["env"]["ANTHROPIC_BASE_URL"] = ds["ANTHROPIC_BASE_URL"]
open(sys.argv[1], "w").write(
    json.dumps(spec, indent=2, ensure_ascii=False))
EOF
chmod 600 "$RUN_DIR/spec.json"

# 3) pre-flight probe: one model call through the assembled context
$PY -m scientist.cli --spec "$RUN_DIR/spec.json" \
    --world "$RUN_DIR/world" --probe

# 3b) seat-channel preflight through the run runtime (scratch root) — the
# exact environment a seat gets (stripped ambient, scratch .claude/home,
# explicit --model). A broken channel aborts HERE instead of dying as
# per-seat failures mid-run. Empty or wrong credentials fail loudly;
# a silent fallback into the user's settings is impossible by
# construction (proved 2026-09-01: empty runtime -> "Not logged in").
(
    SEAT_MODEL=$($PY -c "import json;print(json.load(open('$RUN_DIR/spec.json'))['assistant'].get('model') or '')")
    SEAT_ENV=$($PY -c "import json;print(' '.join(f'{k}={v}' for k,v in json.load(open('$RUN_DIR/spec.json'))['assistant'].get('env',{}).items()))")
    mkdir -p "$RUN_DIR/seats/.claude" "$RUN_DIR/seats/home"
    $PY -c "import json,os,sys; \
json.dump({'env': json.load(open('$RUN_DIR/spec.json'))['assistant'].get('env') or {}}, \
open('$RUN_DIR/seats/.claude/settings.json','w'), indent=2); \
open('$RUN_DIR/seats/home/.gitconfig','w').write('[user]\n\tname = preflight\n\temail = preflight@run.invalid\n')"
    chmod 600 "$RUN_DIR/seats/.claude/settings.json"
    RC=0
    env -i PATH="$PATH" HOME="$RUN_DIR/seats/home" \
        CLAUDE_CONFIG_DIR="$RUN_DIR/seats/.claude" $SEAT_ENV \
        timeout 120 claude -p 'reply with exactly: ok' \
        --input-format text --output-format json \
        ${SEAT_MODEL:+--model "$SEAT_MODEL"} \
        > "$RUN_DIR/seat_preflight.json" 2>"$RUN_DIR/seat_preflight.err" || RC=$?
    $PY - "$RC" "$RUN_DIR" <<'PYEOF' || exit 1
import json, sys
rc, run_dir = int(sys.argv[1]), sys.argv[2]
try:
    d = json.load(open(f"{run_dir}/seat_preflight.json"))
except Exception:
    sys.exit(f"seat preflight: no parseable reply (rc={rc}) — "
             f"channel broken, aborting launch")
if d.get("is_error") or str(d.get("result", "")).strip() != "ok":
    sys.exit(f"seat preflight failed: {str(d.get('result'))[:200]}")
print("seat preflight: channel OK through the run world's own runtime")
PYEOF
) || { echo "seat-channel preflight FAILED — see $RUN_DIR/seat_preflight.*" >&2; exit 1; }

# escape hatch for testing the preflights without launching a run
if [ "${PREFLIGHT_ONLY:-0}" = "1" ]; then
    echo "PREFLIGHT_ONLY=1 — both channels verified, not launching"
    exit 0
fi

# 4) the scientist (detached) + read-only snapshot sidecar (detached)
#    Run-by-run isolation for the PI process itself, applied AFTER the
#    spec is built (credential assembly needs the real HOME): no ambient
#    session identity, no user HOME. The seat side enforces the same at
#    its own spawn chokepoint — see assistant_tools._world_runtime.
for v in $(env | grep -oE '^(CLAUDE|ANTHROPIC)[A-Za-z0-9_]*'); do
    unset "$v"
done
export HOME="$RUN_DIR/seats/home"
mkdir -p "$HOME"
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
