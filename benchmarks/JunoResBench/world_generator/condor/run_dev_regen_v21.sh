#!/usr/bin/env bash
# Regenerate one shard's lost dev split into $base/dev_regen/shard_XXX.
# args: task seed shard shards dev_root
set -euo pipefail
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
export PYTHONNOUSERSITE=1
task="$1"; seed="$2"; shard="$3"; shards="$4"; dev_root="$5"
id=$(printf '%03d' "$shard")
out="$dev_root/shard_${id}"
mkdir -p "$out"
cd "$JRB_REPO_ROOT"
/usr/bin/python3 "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/shard_dev_regen.py" \
  --task "$task" --seed "$seed" --geometry-mode juno \
  --calibration-events-per-point 20 \
  --probe-events-per-point 200 --controls 7680 \
  --shard "$shard" --shards "$shards" --out "$out"
