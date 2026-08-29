#!/usr/bin/env bash
# Initialize the junoresbench_full_std_opt target repo as a real git repo
# (needed once: singlenode/simpleevo resolve the root node SHA from `git
# rev-parse HEAD`). The runtime image is REUSED from examples/xsbench_opt
# (python3.9 + node/claude + git; numpy arrives via the frozen pyuser
# asset, see README.md) — no image build for this task.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

cd "$HERE/repo"
if [ -d .git ]; then
    echo "repo already a git repo; nothing to do."
else
    git init -q
    git add -A
    git -c user.email=junoresbench@example.invalid \
        -c user.name=junoresbench commit -qm "jrb full-readout electron baseline (standard mode)"
    echo "repo initialized at $HERE/repo ($(git rev-parse --short HEAD))"
fi
