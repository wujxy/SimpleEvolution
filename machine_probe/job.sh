#!/usr/bin/env bash
set -uo pipefail
source /lustrefs/juno26/users/lidian/SimpleEvolution/machine_probe/job_env.sh
exec /lustrefs/juno26/users/lidian/py_venv/miniconda3/bin/python3 -m experiment.cli --manifest /lustrefs/juno26/users/lidian/SimpleEvolution/machine_probe/manifest.json --backend condor --job-id "$1"
