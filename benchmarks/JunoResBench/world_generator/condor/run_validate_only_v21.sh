#!/usr/bin/env bash
# Re-run the v2.1 validator on an existing published release.
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
PY=/usr/bin/python3
export PYTHONPATH=/lustrefs/juno26/users/lidian/pylibs/jrb_py39${PYTHONPATH:+:$PYTHONPATH}

TASK=electron_single_site
BASE=/scratchfs2/juno/lidian/jrb_v21/preflight

cd "$JRB_REPO_ROOT"
$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/validate_release.py" \
  --task "$TASK" --release "$BASE/release" --output "$BASE/validation"
[ -f "$BASE/validation/ACCEPTED" ] && echo "PREFLIGHT: ACCEPTED" || { echo "PREFLIGHT: REJECTED"; exit 1; }
