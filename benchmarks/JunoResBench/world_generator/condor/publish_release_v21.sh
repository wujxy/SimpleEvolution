#!/usr/bin/env bash
# Publish the v2.1 electron shard bank in place and run acceptance:
# sha256 gate, bookkeeping (truth/labels/config/manifest), symlink tree,
# then validate_release. Zero waveform copies — shards ARE the release.
# Run AFTER every shard has finished (240 shard_manifest.json files).
#   usage: publish_release_v21.sh
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
LUSTRE_BASE=/lustrefs/juno26/users/lidian/jrb_v21/electron
JUNOFS_BASE=/junofs/users/lidian/jrb_v21/electron
RELEASE=$LUSTRE_BASE/release
VALIDATION=$LUSTRE_BASE/validation

cd "$JRB_REPO_ROOT"

n_manifest=$(cat "$LUSTRE_BASE"/shards/shard_*/shard_manifest.json "$JUNOFS_BASE"/shards/shard_*/shard_manifest.json 2>/dev/null | wc -l)
[ "$n_manifest" -ge "$SHARDS" ] || { echo "only $n_manifest/$SHARDS shard manifests — array incomplete"; exit 1; }

# two-root shard view: shards 000-149 on lustrefs, 150-239 on junofs
rm -rf "$LUSTRE_BASE/merge_view"
mkdir -p "$LUSTRE_BASE/merge_view"
i=0
while [ "$i" -lt "$SHARDS" ]; do
    id=$(printf '%03d' "$i")
    if [ "$i" -lt 150 ]; then
        ln -s "$LUSTRE_BASE/shards/shard_${id}" "$LUSTRE_BASE/merge_view/shard_${id}"
    else
        ln -s "$JUNOFS_BASE/shards/shard_${id}" "$LUSTRE_BASE/merge_view/shard_${id}"
    fi
    i=$((i+1))
done

rm -rf "$RELEASE" "$VALIDATION"
# publish mode: shards stay in place on scratchfs2/junofs, symlinked into
# the release tree; only truth/labels/config/manifests are written
$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/shard/publish_release.py" \
  --task "$TASK" --seed "$SEED" --geometry-mode juno \
  --calibration-events-per-point 20 \
  --probe-events-per-point 200 --controls 7680 \
  --shards-root "$LUSTRE_BASE/merge_view" --out "$RELEASE" --publish-shards
# publish mode bookkeeping (~100 MB) lands on lustrefs; the ~420 GiB of
# waveform shards stay in place on scratchfs2 + junofs, symlinked into the
# release tree, and are cleaned only after the bank is superseded

$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/validate_release.py" \
  --task "$TASK" --release "$RELEASE" --output "$VALIDATION"
[ -f "$VALIDATION/ACCEPTED" ] && echo "FINAL: ELECTRON v2.1 RELEASE ACCEPTED" || echo "FINAL: REJECTED — see $VALIDATION"
