#!/usr/bin/env bash
# Launch one singlenode arm on the OMILREC v1.0.0 optimization task:
# a PRODUCTION C++ maximum-likelihood reconstruction algorithm, a frozen
# four-gate correctness contract, SPEED_MS minimization (target 170
# ms/evt against the frozen 919.9 baseline on this machine). The world
# is the task repo (examples/omilrec_opt/repo, v1.0.0 at 8bbf2f5); the
# human-expert reference (examples/omilrec_opt/reference/) stays outside
# every container view.
#
#   bash examples/omilrec_sci_opt/launch_singlenode.sh scientist RUN_DIR
#   bash examples/omilrec_sci_opt/launch_singlenode.sh coding    RUN_DIR
#
# Differences from the JRB launcher, all in the container shape (the
# generic hooks live in singlenode/node_common.sh + smoke.sh):
#   - NODE_IMAGE = examples/omilrec_opt/apptainer.sif — almalinux:9 plus
#     the system-library chain the JUNO shared libs link against; the
#     JUNO toolchain itself comes from /cvmfs, bound read-only;
#   - WORLD_RW overlays the NESTED editable surface and the eval's own
#     write faces (build/ InstallArea/ TEMP/) over the :ro world —
#     tests/, scripts/, baseline/ stay EROFS;
#   - EXTRA_RO_BINDS = /cvmfs plus exactly the two /data/juno/dingxf
#     data trees the eval reads (OMILREC_maps + inputs). The wider
#     dingxf tree (prior experiment output) is NOT bound — the v3
#     sibling-leak lesson, applied at the mount table;
#   - the snapshot loop tracks OMILRECV2/src (not src/);
#   - TASKSET_RANGE pins each arm to one socket (this host is 2-socket /
#     2-NUMA, 128 CPUs): scientist -> node 0, coding -> node 1. With two
#     arms benchmarking concurrently, disjoint per-socket ranges keep one
#     arm's build bursts and benches out of the other's timing — SPEED_MS
#     is a wall-clock metric. Symmetric 32-core allowances keep the arms'
#     build speeds comparable too.
#
# WALL defaults to the 7-day safety cap, not a target: the scientist arm
# ends when the scientist concludes; the coding arm runs to its wall.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

ARM=${1:?usage: launch_singlenode.sh scientist|coding RUN_DIR}
RUN_DIR=${2:?usage: launch_singlenode.sh scientist|coding RUN_DIR}
case "$ARM" in
    scientist) export TASKSET_RANGE=${TASKSET_RANGE:-"0-31,64-95"} ;;
    coding)    export TASKSET_RANGE=${TASKSET_RANGE:-"32-63,96-127"}
               export TASK_FILE="$HERE/coding_task.txt" ;;
    *) echo "arm must be scientist or coding" >&2; exit 2 ;;
esac

export NODE_IMAGE="$REPO_ROOT/examples/omilrec_opt/apptainer.sif"
export NODE_TEMPLATE="$REPO_ROOT/examples/omilrec_opt/repo"
export SPEC_TEMPLATE="$HERE/spec.json"
export WORLD_RW="OMILRECV2/src .scientist .git build InstallArea TEMP"
export EXTRA_RO_BINDS="/cvmfs /data/juno/dingxf/OMILREC_maps /data/juno/dingxf/inputs"
export SNAPSHOT_SUBDIR="OMILRECV2/src"
export WALL=${WALL:-604800}
# pytest note: the eval's gate suites run under the cvmfs Python 3.11
# (the JUNO setup repoints python); the sif ships pytest for it at
# /usr/local/lib/cvmfs_python311_extra via its own PYTHONPATH, which
# node_scientist_env preserves (prepend-merge). Nothing to install.

# Smoke-gate layout overrides (defaults in smoke.sh stay jrb/xsbench).
export S2_CHECK='[ -d /work/OMILRECV2/src ] && [ -f /spec.json ] && [ -d /repo/scripts ] && [ -d /scratch ] && [ -w /scratch ] && [ -x /work/scripts/sl_eval_v100.sh ]'
export S3_FROZEN='/work/scripts/.smoke /work/README.md /work/tests/.smoke /work/baseline/.smoke'
export S3_EDITABLE_RW='/work/OMILRECV2/src /work/build /work/InstallArea /work/TEMP'
# S7 = a real (short) eval: from-zero build + all four gates + a 10-event
# benchmark. evtmax 10 reads slower than the 100-event baseline — the
# gate only requires EVAL_RESULT=ok.
export S7_BENCH_CMD='bash /work/scripts/sl_eval_v100.sh --evtmax 10 2>&1 | tee /tmp/s7eval.log | tail -8 && grep -q "EVAL_RESULT=ok" /tmp/s7eval.log && echo BENCH-OK'

exec bash "$REPO_ROOT/singlenode/run_${ARM}.sh" "$RUN_DIR"
