#!/usr/bin/env bash
# singlenode — one container, one world, one agent. Shared machinery for
# both modes (scientist / coding agent); sourced by run_scientist.sh,
# run_coding.sh and smoke.sh. Zero simpleevo dependency: the world
# template and the image are referenced by path (defaults point at the
# xsbench example; override NODE_IMAGE / NODE_TEMPLATE / SPEC_TEMPLATE).
#
# Mount semantics (identical for both modes — cross-mode comparability
# starts at the filesystem): /work is a ro base with THREE rw overlays
# (src/, .scientist/, .git/) — scripts/, benchmarks/, README are EROFS;
# /repo is the frozen template reference; /scratch is free space; the
# container's entire host visibility is $RUN_DIR, the template, the spec
# file, and the image.
SINGLENODE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=${REPO_ROOT:-$(dirname "$SINGLENODE_DIR")}
NODE_IMAGE=${NODE_IMAGE:-$REPO_ROOT/examples/xsbench_opt/apptainer.sif}
NODE_TEMPLATE=${NODE_TEMPLATE:-$REPO_ROOT/examples/xsbench_opt/repo}
SPEC_TEMPLATE=${SPEC_TEMPLATE:-$REPO_ROOT/examples/xsbench_opt/spec.json}

# Nested-container hygiene (this machine's shell runs inside an outer
# apptainer): call BEFORE exporting your own APPTAINERENV_*.
node_unset_inherited_binds() {
    unset APPTAINER_BIND SINGULARITY_BIND APPTAINERENV_APPTAINER_BIND \
        2>/dev/null || true
    while IFS='=' read -r v _; do unset "$v"; done \
        < <(env | grep -E '^(APPTAINERENV_|SINGULARITYENV_)' || true)
}

# The one-container argv. Requires RUN_DIR set by the caller. The frozen
# scientist package binds only when present (the coding mode has none —
# claude IS the agent there).
node_container() {
    local extra=()
    if [ -d "$RUN_DIR/pkg/scientist" ]; then
        extra+=(--bind "$RUN_DIR/pkg/scientist:/opt/scientist/scientist:ro")
    fi
    apptainer exec --cleanenv --no-eval --userns --containall \
        --no-mount cwd,home,hostfs --cwd /work \
        --bind "$RUN_DIR/world:/work:ro" \
        --bind "$RUN_DIR/world/src:/work/src:rw" \
        --bind "$RUN_DIR/world/.scientist:/work/.scientist:rw" \
        --bind "$RUN_DIR/world/.git:/work/.git:rw" \
        --bind "$NODE_TEMPLATE:/repo:ro" \
        --bind "$RUN_DIR/scratch:/scratch:rw" \
        --bind "$RUN_DIR/spec.json:/spec.json:ro" \
        --bind "$RUN_DIR/home:/home/wujxy:rw" \
        "${extra[@]}" \
        "$NODE_IMAGE" "$@"
}

# Runtime env injection (never credentials for the scientist CLI — those
# ride in the spec; the coding mode calls node_coding_env instead).
node_scientist_env() {
    export APPTAINERENV_PYTHONPATH=/opt/scientist
    export APPTAINERENV_PYTHONPYCACHEPREFIX=/scratch/pycache
    export APPTAINERENV_CLAUDE_CONFIG_DIR=/scratch/claude-config
    [ -n "${BENCH_PIN:-}" ] && export APPTAINERENV_BENCH_PIN="$BENCH_PIN"
    return 0
}

# Coding-agent mode: claude is the main process, so its credentials DO
# travel via APPTAINERENV_* (the coding-arm executor_environment recipe:
# token + base_url, isolated config dir, nothing else).
node_coding_env() {
    node_scientist_env
    export APPTAINERENV_ANTHROPIC_AUTH_TOKEN="$ANTHROPIC_AUTH_TOKEN"
    export APPTAINERENV_ANTHROPIC_BASE_URL="$ANTHROPIC_BASE_URL"
    return 0
}

# Per-run layout shared by both launchers.
node_prepare_run_dir() {
    mkdir -p "$RUN_DIR"/{scratch,snapshots,pkg,home} \
        "$RUN_DIR/scratch/claude-config"
    cp -a "$NODE_TEMPLATE" "$RUN_DIR/world"
    mkdir -p "$RUN_DIR/world/.scientist"   # bind sources must exist
    git_name=$(git config --global user.name || echo wujxy)
    git_mail=$(git config --global user.email || echo "wujxy@st.usst.edu.cn")
    printf '[user]\n\tname = %s\n\temail = %s\n' "$git_name" "$git_mail" \
        > "$RUN_DIR/home/.gitconfig"
    chmod 700 "$RUN_DIR/home"
}
