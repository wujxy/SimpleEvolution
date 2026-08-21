#!/usr/bin/env bash
# Initialize the omilrec_opt target repo and runtime.
#
# Unlike xsbench_opt, the OMILREC eval is not self-contained: it needs the JUNO
# software stack (mounted from /cvmfs) and the benchmark input + reconstruction
# maps (under /data/juno/dingxf). Check those first — the eval cannot run
# without them — then make sure the target repo is a real git repo (once) and
# the Apptainer runtime image is built.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. Host prerequisites (JUNO toolchain + bench data).
MISSING=0
if [ ! -d /cvmfs/juno.ihep.ac.cn ]; then
  echo "ERROR: /cvmfs (JUNO software stack) is not mounted on this host." >&2
  echo "  The eval sources /cvmfs/juno.ihep.ac.cn/el9_amd64_gcc11/Release/J26.1.1/setup.sh." >&2
  MISSING=1
fi
if [ ! -f /data/juno/dingxf/inputs/index_12628_rtraw_1.json ]; then
  echo "ERROR: benchmark input not found: /data/juno/dingxf/inputs/index_12628_rtraw_1.json" >&2
  echo "  (the eval also needs /data/juno/dingxf/OMILREC_maps; set OMILRECV2_TEST_INPUT /" >&2
  echo "   OMILRECV2_TEST_RECMAP to override, and update task.yaml read_only_binds accordingly.)" >&2
  MISSING=1
fi
if [ ! -d /data/juno/dingxf/OMILREC_maps ]; then
  echo "ERROR: reconstruction maps not found: /data/juno/dingxf/OMILREC_maps" >&2
  MISSING=1
fi
if [ "$MISSING" -ne 0 ]; then
  exit 1
fi
echo "host prerequisites OK: /cvmfs, /data/juno/dingxf"

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
