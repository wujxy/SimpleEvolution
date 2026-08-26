#!/usr/bin/env bash
# Initialize the omilrec_opt target repo and runtime.
#
# The only host prerequisite is the JUNO software stack (/cvmfs): the benchmark
# input and reconstruction maps are repo-local (repo/assets/ — EOS-direct-read
# input json + full RecMap, sha256-pinned by tests/reference/manifest.json), so
# no /data bind is needed. Check /cvmfs, make sure the target repo is a real
# git repo (once), then build the Apptainer runtime image if missing (only
# needed on hosts without the JUNO system libraries; IHEP login nodes can run
# the eval bare).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. Host prerequisites (JUNO toolchain only).
MISSING=0
if [ ! -d /cvmfs/juno.ihep.ac.cn ]; then
  echo "ERROR: /cvmfs (JUNO software stack) is not mounted on this host." >&2
  echo "  The eval sources /cvmfs/juno.ihep.ac.cn/el9_amd64_gcc11/Release/J26.1.1/setup.sh." >&2
  MISSING=1
fi
if [ ! -f "$HERE/repo/assets/inputs/index_12628_eos.json" ]; then
  echo "ERROR: benchmark input index missing: $HERE/repo/assets/inputs/index_12628_eos.json" >&2
  MISSING=1
fi
if [ ! -d "$HERE/repo/assets/OMILREC_maps" ]; then
  echo "ERROR: reconstruction maps missing: $HERE/repo/assets/OMILREC_maps" >&2
  echo "  (large .root maps are Git LFS pointers — run 'git lfs pull' inside repo/)" >&2
  MISSING=1
fi
if [ "$MISSING" -ne 0 ]; then
  exit 1
fi
echo "host prerequisites OK: /cvmfs + repo-local assets"

# 2. Target repo -> git repository (once).
cd "$HERE/repo"
if [ -d .git ]; then
  echo "repo already a git repo ($(git rev-parse --short HEAD)); nothing to do."
else
  git init -q
  git add -A
  git -c user.email=omilrec@example.invalid -c user.name=omilrec commit -qm "omilrec v1.0.0 baseline"
  echo "repo initialized at $HERE/repo ($(git rev-parse --short HEAD))"
fi

# 3. Apptainer runtime image (optional; only if not already built).
# Skipped unless explicitly requested (BUILD_IMAGE=1): IHEP login nodes already
# ship the JUNO system libraries, so the eval runs bare; and fakeroot builds
# fail on filesystems without the xattr/symlink semantics fakeroot needs
# (e.g. this Lustre). Only build the image on a host that lacks the JUNO libs.
if [ -f "$HERE/apptainer.sif" ]; then
  echo "runtime image already built: $HERE/apptainer.sif"
  exit 0
fi
if [ "${BUILD_IMAGE:-0}" != "1" ]; then
  echo "skipping image build (IHEP login nodes run the eval bare)."
  echo "to force a build: BUILD_IMAGE=1 $0   (or reuse SimpleLoop's junosw-apptainer.sif)"
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
