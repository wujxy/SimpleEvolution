#!/usr/bin/env bash
# v2.1 preflight: one real-geometry shard at tiny counts, merged and validated.
# The full 240-shard array is only submitted after this prints ACCEPTED.
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
PY=/usr/bin/python3
# matplotlib for the validator atlas, installed for the pinned system python
export PYTHONPATH=/lustrefs/juno26/users/lidian/pylibs/jrb_py39${PYTHONPATH:+:$PYTHONPATH}

TASK=electron_single_site
SEED=20260907
# unique run dir per condor cluster: hep_sub retry clones land on other
# schedds and cannot be removed from here — a shared fixed dir would let two
# clones interleave writes into one shard
BASE=/scratchfs2/juno/lidian/jrb_v21/preflight_${_CONDOR_CLUSTER_ID:-manual$$}

cd "$JRB_REPO_ROOT"
# never rm the whole BASE: hep_sub writes its stdout/stderr logs inside it
rm -rf "$BASE/shards" "$BASE/release" "$BASE/validation"
mkdir -p "$BASE/shards/shard_0"

$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/shard_generate.py" \
  --task "$TASK" --seed "$SEED" --geometry-mode juno \
  --calibration-events-per-point 2 \
  --probe-events-per-point 2 --controls 64 \
  --shard 0 --shards 1 \
  --out "$BASE/shards/shard_0"

$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/merge_release.py" \
  --task "$TASK" --seed "$SEED" --geometry-mode juno \
  --calibration-events-per-point 2 \
  --probe-events-per-point 2 --controls 64 \
  --shards-root "$BASE/shards" --out "$BASE/release"

$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/validate_release.py" \
  --task "$TASK" --release "$BASE/release" --output "$BASE/validation"

[ -f "$BASE/validation/ACCEPTED" ] && echo "PREFLIGHT: ACCEPTED" || { echo "PREFLIGHT: REJECTED"; exit 1; }
