#!/usr/bin/env bash
# 等 240 片全部落盘后自动合并 + 自证验收。
set -uo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
n=240
while true; do
  c=$(find /scratchfs2/juno/lidian/jrb_v21/electron/shards /junofs/users/lidian/jrb_v21/electron/shards -name shard_manifest.json 2>/dev/null | wc -l)
  echo "$(date +%H:%M) manifests $c/$n"
  [ "$c" -ge "$n" ] && break
  sleep 120
done
echo "$(date +%H:%M) ALL SHARDS DONE — publishing"
"$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/condor/publish_release_v21.sh"
rc=$?
echo "$(date +%H:%M) FINISHER exit=$rc"
exit $rc
