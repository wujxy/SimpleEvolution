#!/usr/bin/env bash
set -euo pipefail

task="$1"
seed="$2"
n_pmt="$3"
calibration="$4"
probes="$5"
controls="$6"
output="$7"

: "${JRB_REPO_ROOT:?Set JRB_REPO_ROOT to the shared SimpleEvolution checkout}"
python "$JRB_REPO_ROOT/benchmarks/JunoResBench/world_generator/build_task.py" \
  --task "$task" \
  --out "$output" \
  --seed "$seed" \
  --n-pmt "$n_pmt" \
  --calibration-events-per-point "$calibration" \
  --probe-events-per-point "$probes" \
  --controls "$controls"
