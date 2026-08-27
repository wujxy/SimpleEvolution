#!/usr/bin/env bash
# Launch one singlenode arm on the JunoResBench white-box electron task.
#
#   bash examples/junoresbench_wb_opt/launch_singlenode.sh scientist RUN_DIR
#   bash examples/junoresbench_wb_opt/launch_singlenode.sh coding   RUN_DIR
#
# No time limit by default: WALL / the spec's wall_seconds are 7-day
# safety caps, not targets — the arms end when the agent concludes.
# Extra wiring beyond the stock three override variables:
#   - pyuser/  a frozen cp39 numpy user-site, seeded into the run's
#     container home (the reused xsbench image has no numpy under
#     --containall; the task is numpy-only by design);
#   - S7_BENCH_CMD  the jrb smoke S7 (energy_res instead of lookups/s);
#   - TASK_FILE     the coding-arm prompt.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"   # HERE = examples/junoresbench_wb_opt

ARM=${1:?usage: launch_singlenode.sh scientist|coding RUN_DIR}
RUN_DIR=${2:?usage: launch_singlenode.sh scientist|coding RUN_DIR}

# numpy user-site must be in place BEFORE run_*.sh calls
# node_prepare_run_dir (mkdir -p leaves it untouched).
mkdir -p "$RUN_DIR/home/.local/lib/python3.9"
cp -a "$HERE/pyuser/lib/python3.9/site-packages" \
    "$RUN_DIR/home/.local/lib/python3.9/"

export NODE_IMAGE="$REPO_ROOT/examples/xsbench_opt/apptainer.sif"
export NODE_TEMPLATE="$HERE/repo"
export SPEC_TEMPLATE="$HERE/spec.json"
export S7_BENCH_CMD='bash /work/scripts/check_verify.sh 2>&1 | grep -q "verify: PASS" && bash /work/scripts/bench.sh 2>&1 | grep -E "energy_res=[0-9]" | grep -q . && echo BENCH-OK'
export WALL=${WALL:-604800}
export TASK_FILE="$REPO_ROOT/singlenode/specs/jrb_wb_coding_task.txt"

exec bash "$REPO_ROOT/singlenode/run_${ARM}.sh" "$RUN_DIR"
