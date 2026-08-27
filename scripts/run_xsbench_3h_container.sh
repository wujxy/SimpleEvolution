#!/usr/bin/env bash
# 3h container-arm launcher: the ENTIRE scientist run executes inside ONE
# apptainer container (入世容器模式 — the mounts are the boundary; the
# host only opens the world and collects it).
#
#   host launcher  : mounts + frozen package + spec + credentials -> exec
#   in-container   : scientist CLI (image python3.9) runs the episode;
#                    bash is the container's own shell; the assistant
#                    claude is the image's CLI in the SAME container
#   host observability: run.log (stdout redirect), snapshot sidecar, and
#                    the world dir itself — binds are real dirs
#
# Write contract: /work ro base + src/ .scientist/ .git/ rw overlays —
# scripts/, benchmarks/, README are EROFS ("Edit only src" is physical).
# Container /tmp persists for the single exec, then vanishes.
#
# Usage:   bash scripts/run_xsbench_3h_container.sh [RUN_DIR]
# Env:     BENCH_PIN (optional core pin), FORCE=1 (clobber), SMOKE_ONLY=1
# After:   python scripts/replay_xsbench.py \
#              --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
set -euo pipefail
cd /datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution

RUN_DIR=${1:-runs/xsbench-3h/pi-team-container-v1}
WALL=10800
PY=/datafs/users/wujxy/py_venv/my_env/bin/python
REPO_ROOT=/datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution

# 0) nested-container hygiene: this machine's shell is itself inside an
#    apptainer container; inherited binds and injection vars must go
#    before we set our own (unset APPTAINER_BIND + --userns recipe).
unset APPTAINER_BIND SINGULARITY_BIND APPTAINERENV_APPTAINER_BIND 2>/dev/null || true
while IFS='=' read -r v _; do unset "$v"; done \
    < <(env | grep -E '^(APPTAINERENV_|SINGULARITYENV_)' || true)

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

# 1) run layout: the container's entire host visibility will be $RUN_DIR,
#    the frozen template, the spec file, and the sif — nothing else.
mkdir -p "$RUN_DIR"/{scratch,snapshots,pkg,home} "$RUN_DIR/scratch/claude-config"
cp -a examples/xsbench_opt/repo "$RUN_DIR/world"
mkdir -p "$RUN_DIR/world/.scientist"    # bind sources must exist at exec

# 2) bench.sh anchor-comment drift tripwire (the template already carries
#    the fix at fca81a3; this stays as drift detection)
$PY - "$RUN_DIR/world/scripts/bench.sh" <<'EOF'
import sys
path = sys.argv[1]
text = open(path).read()
stale = "tops out ~2.6M lps"
if stale in text:
    raise SystemExit("bench.sh carries the stale ~2.6M anchor — template drifted")
EOF
BASE_SHA=$(git -C "$RUN_DIR/world" rev-parse HEAD)

# 3) spec with live credentials (same recipe as the v3 arm)
$PY - "$RUN_DIR/spec.json" "$BASE_SHA" <<'EOF'
import json, os, sys
spec = json.load(open("examples/xsbench_opt/spec.json"))
tide = json.load(open("runs/tide-demo-1/spec.json"))
ds = json.load(open(os.path.expanduser(
    "~/.claude/settings_ds.json.backup")))["env"]
spec["base_sha"] = sys.argv[2]
spec["model"]["api_key"] = tide["model"]["api_key"]
spec["assistant"]["env"]["ANTHROPIC_AUTH_TOKEN"] = ds["ANTHROPIC_AUTH_TOKEN"]
spec["assistant"]["env"]["ANTHROPIC_BASE_URL"] = ds["ANTHROPIC_BASE_URL"]
open(sys.argv[1], "w").write(
    json.dumps(spec, indent=2, ensure_ascii=False))
EOF
chmod 600 "$RUN_DIR/spec.json"

# 4) freeze the scientist package for this run (self-contained; the
#    container never sees the live repo tree)
cp -a scientist "$RUN_DIR/pkg/scientist"

# 5) per-run HOME: git identity (host mode got it from the global
#    ~/.gitconfig; --cleanenv drops it, and without it every in-world
#    `git commit` fails with "Please tell me who you are")
git_name=$(git config --global user.name || echo wujxy)
git_mail=$(git config --global user.email || echo "wujxy@st.usst.edu.cn")
printf '[user]\n\tname = %s\n\temail = %s\n' "$git_name" "$git_mail" \
    > "$RUN_DIR/home/.gitconfig"
chmod 700 "$RUN_DIR/home"

# shared argv + env (sourced; defines scientist_container)
source scripts/_container_common.sh
container_env_exports
scientist_container true 2>/dev/null | head -0 || true
printf '%s\n' "apptainer exec (see scripts/_container_common.sh for the full argv)" \
    > "$RUN_DIR/container_argv.txt"

# 6) smoke gate — fail-closed: a bad image/mount/env aborts with no run
bash scripts/smoke_container.sh "$RUN_DIR" "$BASE_SHA"
if [ "${SMOKE_ONLY:-0}" = "1" ]; then
    echo "SMOKE_ONLY=1 — stopping after smoke. run_dir=$RUN_DIR"
    exit 0
fi

# 7) pre-flight probe: one model call through the real mounted world
scientist_container python3 -m scientist.cli \
    --spec /spec.json --world /work --repo /repo --scratch /scratch --probe

# 8) the scientist (detached, ONE apptainer exec for the whole run) +
#    host-side snapshot sidecar (binds are real dirs — unchanged).
#    scientist_container is a shell FUNCTION — nohup cannot exec it
#    directly; the detached call re-sources the common file in a bash -c.
export RUN_DIR REPO_ROOT
setsid nohup bash -c \
    'source "$REPO_ROOT/scripts/_container_common.sh"; scientist_container python3 -m scientist.cli --spec /spec.json --world /work --repo /repo --scratch /scratch' \
    < /dev/null >> "$RUN_DIR/run.log" 2>&1 &
AGENT=$!
setsid nohup "$PY" scripts/snapshot_world_loop.py \
    --world "$RUN_DIR/world" --out "$RUN_DIR/snapshots" \
    --every 60 --max-seconds "$WALL" \
    >> "$RUN_DIR/snapshot.log" 2>&1 &
SNAP=$!
echo "run_dir=$RUN_DIR agent_pid=$AGENT snapshot_pid=$SNAP wall=${WALL}s base_sha=$BASE_SHA"
echo "stop with: kill -TERM -- -$AGENT   (pgid spans container+python+claude)"
echo "tail: tail -f $RUN_DIR/run.log"
