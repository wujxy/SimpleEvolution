#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TASK_ROOT="$HERE/../../benchmarks/JunoResBench/tasks/electron_single_site"
PKG="$HERE/repo/benchmarks/electron_single_site"

mkdir -p "$PKG/evaluator"
cp -a "$TASK_ROOT/TASK.md" "$PKG/TASK.md"
for name in evaluate.py scoring.py sparse_reader.py submission_api.py submission_worker.py; do
    cp -a "$TASK_ROOT/evaluator/$name" "$PKG/evaluator/$name"
done
if [ ! -L "$PKG/data" ]; then
    ln -s /data/jrb/electron_single_site_public "$PKG/data"
fi

cd "$HERE/repo"
if [ ! -d .git ]; then
    git init -q
fi
git add -A
if ! git diff --cached --quiet; then
    git -c user.email=junoresbench@example.invalid \
        -c user.name=junoresbench commit -qm \
        "jrb electron single-site baseline (standard mode)"
fi
echo "repo ready at $HERE/repo ($(git rev-parse --short HEAD))"
