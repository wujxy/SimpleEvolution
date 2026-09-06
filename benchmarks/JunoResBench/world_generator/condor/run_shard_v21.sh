#!/usr/bin/env bash
# v2.1 dense shard generation for the electron_single_site release.
#   args: TASK SEED SHARD SHARDS OUT
# Counts and real LPMT geometry are pinned here on purpose: every shard of
# one release must reproduce identical populations and the same detector.
set -euo pipefail
task="$1"; seed="$2"; shard="$3"; shards="$4"; out="$5"
probes=200
controls=7680
calib=20

: "${JRB_REPO_ROOT:?Set JRB_REPO_ROOT to the SimpleEvolution checkout}"

# frozen interpreter for a reproducible release: the submit shell may carry a
# user conda env in PATH (hep_sub propagates it) — pin the cluster system
# python (3.9 + numpy 1.23) which is identical on every worker node
PY=/usr/bin/python3

echo "=== $(hostname) $(date) shard ${shard}/${shards} task=${task} ==="
$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/shard_generate.py" \
  --task "$task" \
  --seed "$seed" \
  --geometry-mode juno \
  --calibration-events-per-point "$calib" \
  --probe-events-per-point "$probes" \
  --controls "$controls" \
  --shard "$shard" \
  --shards "$shards" \
  --out "$out"
echo "=== shard ${shard} done $(date) ==="
