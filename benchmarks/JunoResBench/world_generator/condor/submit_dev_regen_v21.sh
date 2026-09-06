#!/usr/bin/env bash
# Submit the 240-way dev-regen array. Outputs land beside the shards:
#   0-149 -> scratchfs2/dev_regen, 150-239 -> junofs/dev_regen
# A login-node step later moves each dev/ into the lustrefs shard tree.
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution

TASK=electron_single_site
SEED=20260907
SHARDS=240
SCRATCH_BASE=/scratchfs2/juno/lidian/jrb_v21/electron
JUNOFS_BASE=/junofs/users/lidian/jrb_v21/electron

cd "$(dirname "$0")"
mkdir -p "$SCRATCH_BASE/dev_regen" "$SCRATCH_BASE/logs" "$JUNOFS_BASE/dev_regen" "$JUNOFS_BASE/logs"

for i in $(seq 0 $((SHARDS-1))); do
    if [ "$i" -lt 150 ]; then base="$SCRATCH_BASE"; else base="$JUNOFS_BASE"; fi
    id=$(printf '%03d' "$i")
    hep_sub -g juno -os AlmaLinux9 -np 1 -wt short \
      -o "$base/logs/dev_${id}" -e "$base/logs/dev_${id}" \
      -name jrb21-dev-${id} \
      "$PWD/run_dev_regen_v21.sh" \
      -argu "$TASK" "$SEED" "$i" "$SHARDS" "$base/dev_regen" \
      >/dev/null || { echo "submit failed at shard $i"; exit 1; }
    sleep 0.2
done
echo "submitted $SHARDS dev-regen jobs"
