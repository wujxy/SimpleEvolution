#!/usr/bin/env bash
# Launch one singlenode arm on the JunoResBench FULL-READOUT white-box
# electron task (all 17612 channels digitized every window).
#
#   bash examples/junoresbench_full_opt/launch_singlenode.sh scientist RUN_DIR
#   bash examples/junoresbench_full_opt/launch_singlenode.sh coding   RUN_DIR
#
# No time limit by default: WALL / the spec's wall_seconds are 7-day
# safety caps, not targets — the arms end when the agent concludes.
# The numpy user-site asset is REUSED from examples/junoresbench_wb_opt.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"   # HERE = examples/junoresbench_full_opt
WB="$REPO_ROOT/examples/junoresbench_wb_opt"

ARM=${1:?usage: launch_singlenode.sh scientist|coding RUN_DIR}
RUN_DIR=${2:?usage: launch_singlenode.sh scientist|coding RUN_DIR}

# numpy user-site must be in place BEFORE run_*.sh calls
# node_prepare_run_dir (mkdir -p leaves it untouched).
mkdir -p "$RUN_DIR/home/.local/lib/python3.9"
cp -a "$WB/pyuser/lib/python3.9/site-packages" \
    "$RUN_DIR/home/.local/lib/python3.9/"

export NODE_IMAGE="$REPO_ROOT/examples/xsbench_opt/apptainer.sif"
export NODE_TEMPLATE="$HERE/repo"
export SPEC_TEMPLATE="$HERE/spec.json"
export S7_BENCH_CMD='bash /work/scripts/check_verify.sh 2>&1 | grep -q "verify: PASS" && bash /work/scripts/bench.sh 2>&1 | grep -E "energy_res=[0-9]" | grep -q . && echo BENCH-OK'
export WALL=${WALL:-604800}
export TASK_FILE="$REPO_ROOT/singlenode/specs/jrb_full_coding_task.txt"

exec bash "$REPO_ROOT/singlenode/run_${ARM}.sh" "$RUN_DIR"
