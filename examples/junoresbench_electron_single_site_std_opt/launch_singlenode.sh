#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
WB="$REPO_ROOT/examples/junoresbench_wb_opt"

ARM=${1:?usage: launch_singlenode.sh scientist|coding RUN_DIR}
RUN_DIR=${2:?usage: launch_singlenode.sh scientist|coding RUN_DIR}

mkdir -p "$RUN_DIR/home/.local/lib/python3.9"
cp -a "$WB/pyuser/lib/python3.9/site-packages" \
    "$RUN_DIR/home/.local/lib/python3.9/"

DEFAULT_JRB_MOUNT="/home/wujxy/mnt/lustrefs_juno26/users/lidian/jrb_v2/production/electron_single_site/release/public:/data/jrb/electron_single_site_public"
if [ -n "${JRB_ELECTRON_PUBLIC:-}" ]; then
    export EXTRA_RO_MOUNTS="$JRB_ELECTRON_PUBLIC:/data/jrb/electron_single_site_public"
else
    export EXTRA_RO_MOUNTS="$DEFAULT_JRB_MOUNT"
fi

export NODE_IMAGE="$REPO_ROOT/examples/xsbench_opt/apptainer.sif"
export NODE_TEMPLATE="$HERE/repo"
export SPEC_TEMPLATE="$HERE/spec.json"
export TASK_FILE="$REPO_ROOT/singlenode/specs/jrb_electron_single_site_coding_task.txt"
export WALL=${WALL:-604800}
export S2_CHECK='[ -d /work/src ] && [ -d /work/benchmarks/electron_single_site/data/dev ] && [ -f /spec.json ] && [ -d /scratch ] && [ -w /scratch ]'
export S3_FROZEN='/work/scripts/.smoke /work/README.md /work/benchmarks/.smoke'
export S7_BENCH_CMD='bash /work/scripts/check_verify.sh 2>&1 | grep -q "verify: PASS" && bash /work/scripts/bench.sh 2>&1 | grep -E "R_1MeV_percent=[0-9]" | grep -q . && echo BENCH-OK'
export S9_REQUIRED='[ -d /data/jrb/electron_single_site_public ] && [ ! -e /data/jrb/electron_single_site_public/../private ]'

exec bash "$REPO_ROOT/singlenode/run_${ARM}.sh" "$RUN_DIR"
