#!/usr/bin/env bash
# Launch one singlenode arm on the JunoResBench FULL-READOUT standard-mode
# electron task (all 17612 channels digitized every window; the package
# carries data, calibration labels and scorer only — no generator; inside
# the container it appears under the neutral name benchmarks/electron_full/).
#
#   bash examples/junoresbench_full_std_opt/launch_singlenode.sh scientist RUN_DIR
#   bash examples/junoresbench_full_std_opt/launch_singlenode.sh coding   RUN_DIR
#
# No time limit by default: WALL / the spec's wall_seconds are 7-day
# safety caps, not targets — the arms end when the agent concludes.
# The numpy user-site asset is REUSED from examples/junoresbench_wb_opt.
#
# Isolation contract (see docs/design/JRB-standard-oracle两模式定案.md):
# mounts are the boundary — this template repo ships the task package
# only, so the /repo:ro bind exposes no generator; host-side truth and
# the canonical juno_res_bench/ source stay outside the container.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"   # HERE = examples/junoresbench_full_std_opt
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
export TASK_FILE="$REPO_ROOT/singlenode/specs/jrb_full_std_coding_task.txt"

exec bash "$REPO_ROOT/singlenode/run_${ARM}.sh" "$RUN_DIR"
