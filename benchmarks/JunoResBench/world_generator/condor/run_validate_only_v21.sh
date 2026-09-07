#!/usr/bin/env bash
# Re-run the v2.1 validator on an existing published release.
set -euo pipefail
export PATH=/afs/ihep.ac.cn/soft/common/sysgroup/hep_job/bin:$PATH
export JRB_REPO_ROOT=/lustrefs/juno26/users/lidian/SimpleEvolution
PY=/usr/bin/python3
export PYTHONPATH=/lustrefs/juno26/users/lidian/pylibs/jrb_py39${PYTHONPATH:+:$PYTHONPATH}

TASK=electron_single_site
RELEASE=${1:?usage: run_validate_only_v21.sh <release-dir> [<output-dir>]}"
OUTPUT=${2:-$RELEASE/validation}

cd "$JRB_REPO_ROOT"
$PY "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/validate_release.py" \
  --task "$TASK" --release "$RELEASE" --output "$OUTPUT"
[ -f "$OUTPUT/ACCEPTED" ] && echo "VALIDATION: ACCEPTED" || { echo "VALIDATION: REJECTED"; exit 1; }
