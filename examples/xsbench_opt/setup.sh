#!/usr/bin/env bash
# Initialize the xsbench_opt target repo as a real git repo (needed once:
# SimpleEvolution resolves the root Node SHA from `git rev-parse HEAD` and
# clones the repo with `git clone --local`).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. Target repo -> git repository (once).
cd "$HERE/repo"
if [ -d .git ]; then
  echo "repo already a git repo; nothing to do."
else
  git init -q
  git add -A
  git -c user.email=xsbench@example.invalid -c user.name=xsbench commit -qm "xsbench baseline"
  echo "repo initialized at $HERE/repo ($(git rev-parse --short HEAD))"
fi

# 2. Apptainer runtime image (optional; only if not already built).
if [ -f "$HERE/apptainer.sif" ]; then
  echo "runtime image already built: $HERE/apptainer.sif"
  exit 0
fi
if ! command -v apptainer >/dev/null 2>&1; then
  echo "apptainer not found; skipping image build. Build it later with:"
  echo "  cd $HERE && apptainer build --fakeroot apptainer.sif apptainer.def"
  exit 0
fi
echo "building runtime image..."
cd "$HERE"
# The IHEP JUNO shell runs inside an outer Apptainer container whose
# APPTAINER_BIND/SINGULARITY_BIND points at host paths that do not exist in a
# fresh almalinux base. Unset them so the inner build does not try to bind
# /data, /cvmfs, ... and fail. Harmless everywhere else.
env -u APPTAINER_BIND -u SINGULARITY_BIND \
    apptainer build --fakeroot apptainer.sif apptainer.def
