#!/usr/bin/env bash
# Submit the v2.1 electron shard array (240 shards) via hep_sub.
# Shards 000-149 land on /scratchfs2, 150-239 on /junofs: neither disk alone
# holds the full ~420 GiB shard set.
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution

TASK=electron_single_site
SEED=20260907
SHARDS=240
SCRATCH_BASE=/scratchfs2/juno/lidian/jrb_v21/electron
JUNOFS_BASE=/junofs/users/lidian/jrb_v21/electron

cd "$(dirname "$0")"
mkdir -p "$SCRATCH_BASE/shards" "$SCRATCH_BASE/logs" "$JUNOFS_BASE/shards" "$JUNOFS_BASE/logs"

for i in $(seq 0 $((SHARDS-1))); do
    if [ "$i" -lt 150 ]; then
        base="$SCRATCH_BASE"
    else
        base="$JUNOFS_BASE"
    fi
    id=$(printf '%03d' "$i")
    hep_sub -g juno -os AlmaLinux9 -np 1 -wt short \
      -o "$base/logs/shard_${id}" -e "$base/logs/shard_${id}" \
      -name jrb21-electron-${id} \
      "$PWD/run_shard_v21.sh" \
      -argu "$TASK" "$SEED" "$i" "$SHARDS" "$base/shards/shard_${id}" \
      >/dev/null || { echo "submit failed at shard $i"; exit 1; }
    sleep 0.2
done
echo "submitted $SHARDS shards for $TASK (0-149 -> scratchfs2, 150-239 -> junofs)"
