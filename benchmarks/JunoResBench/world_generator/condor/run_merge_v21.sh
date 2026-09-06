#!/usr/bin/env bash
# Merge the v2.1 electron shards into the release tree and validate.
# Run AFTER every shard has finished (240 shard_manifest.json files).
#   usage: run_merge_v21.sh
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
PY=/usr/bin/python3
# cp39 matplotlib/numpy for the validator atlas, on the shared disk
export PYTHONNOUSERSITE=1
export PYTHONPATH=/junofs/users/lidian/jrb_v21/pylibs${PYTHONPATH:+:$PYTHONPATH}
export MPLCONFIGDIR=/tmp/jrb-mpl-worker

TASK=electron_single_site
SEED=20260907
SHARDS=240
SCRATCH_BASE=/scratchfs2/juno/lidian/jrb_v21/electron
JUNOFS_BASE=/junofs/users/lidian/jrb_v21/electron
RELEASE=/lustrefs/juno26/users/lidian/jrb_v21/electron/release
VALIDATION=/lustrefs/juno26/users/lidian/jrb_v21/electron/validation

cd "$JRB_REPO_ROOT"

n_manifest=$(cat "$SCRATCH_BASE"/shards/shard_*/shard_manifest.json "$JUNOFS_BASE"/shards/shard_*/shard_manifest.json 2>/dev/null | wc -l)
[ "$n_manifest" -ge "$SHARDS" ] || { echo "only $n_manifest/$SHARDS shard manifests — array incomplete"; exit 1; }

# build a two-root shard view so the merger sees one ordered shard_* tree
rm -rf "$SCRATCH_BASE/merge_view"
mkdir -p "$SCRATCH_BASE/merge_view"
i=0
while [ "$i" -lt "$SHARDS" ]; do
    id=$(printf '%03d' "$i")
    if [ "$i" -lt 150 ]; then
        ln -s "$SCRATCH_BASE/shards/shard_${id}" "$SCRATCH_BASE/merge_view/shard_${id}"
    else
        ln -s "$JUNOFS_BASE/shards/shard_${id}" "$SCRATCH_BASE/merge_view/shard_${id}"
    fi
    i=$((i+1))
done

rm -rf "$RELEASE" "$VALIDATION"
$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/merge_release.py" \
  --task "$TASK" --seed "$SEED" --geometry-mode juno \
  --calibration-events-per-point 20 \
  --probe-events-per-point 200 --controls 7680 \
  --shards-root "$SCRATCH_BASE/merge_view" --out "$RELEASE" \
  --dev-out "$JUNOFS_BASE"
# quota split: calibration+final (~358 GiB) on lustrefs, dev (~65 GiB) on junofs
ln -sfn "$JUNOFS_BASE/public/dev" "$RELEASE/public/dev"
# shards (~420 GiB across scratchfs2 + junofs) are cleaned by hand after the
# release is ACCEPTED and copied to its final homes

$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/validate_release.py" \
  --task "$TASK" --release "$RELEASE" --output "$VALIDATION"
[ -f "$VALIDATION/ACCEPTED" ] && echo "FINAL: ELECTRON v2.1 RELEASE ACCEPTED" || echo "FINAL: REJECTED — see $VALIDATION"
