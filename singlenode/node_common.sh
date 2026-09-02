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

# The editable overlays: repo-relative paths bound :rw over the :ro /work
# base (each later bind shadows the ro mount). Default is the xsbench/jrb
# single-src layout; a task with a nested editable surface plus its own
# build outputs (omilrec: OMILRECV2/src + build/ InstallArea/ TEMP/)
# overrides WORLD_RW in its launcher.
WORLD_RW=${WORLD_RW:-src .git}
# Extra read-only host mounts the task's eval needs (e.g. "/cvmfs
# /data/juno/dingxf/OMILREC_maps" for the JUNO toolchain and bench maps).
# Bind exactly what the eval reads — never a wider tree than necessary
# (sibling experiment output must stay invisible to the agent).
EXTRA_RO_BINDS=${EXTRA_RO_BINDS:-}

# The one-container argv. Requires RUN_DIR set by the caller. The frozen
# scientist package binds only when present (the coding mode has none —
# claude IS the agent there).
#
# TASKSET_RANGE (optional): pin the whole container — apptainer and every
# process inside it — to a host core set. For SPEED benchmarks run two
# arms at once on one machine, disjoint per-socket ranges keep one arm's
# build bursts and benches out of the other's timing.
node_container() {
    local prefix=() extra=() p
    if [ -n "${TASKSET_RANGE:-}" ]; then
        prefix+=(taskset -c "$TASKSET_RANGE")
    fi
    if [ -d "$RUN_DIR/pkg/scientist" ]; then
        extra+=(--bind "$RUN_DIR/pkg/scientist:/opt/scientist/scientist:ro")
    fi
    for p in $EXTRA_RO_BINDS; do
        extra+=(--bind "$p:$p:ro")
    done
    local rw_binds=()
    for p in $WORLD_RW; do
        rw_binds+=(--bind "$RUN_DIR/world/$p:/work/$p:rw")
    done
    # The harness body's write channel (three-zone world): the same
    # tree that /work/.scientist shows read-only through the base
    # bind, mounted writable at /state for the scientist CLI alone.
    # No actor prompt ever names /state; the read-only view is what
    # accidental commands hit. docs/design/世界三区设计.md §3.1.
    if [ -d "$RUN_DIR/world/.scientist" ]; then
        rw_binds+=(--bind "$RUN_DIR/world/.scientist:/state:rw")
    fi
    "${prefix[@]}" apptainer exec --cleanenv --no-eval --userns --containall \
        --no-mount cwd,home,hostfs --cwd /work \
        --bind "$RUN_DIR/world:/work:ro" \
        "${rw_binds[@]}" \
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
    # PREPEND /opt/scientist, never replace: an image may ship its own
    # PYTHONPATH (the omilrec sif carries /usr/local/lib/cvmfs_python311_extra
    # — pytest for the cvmfs python its eval uses; APPTAINERENV_* would
    # silently clobber it and the gate suites would lose pytest). The
    # xsbench/jrb images ship none, so the merge is a no-op there.
    local image_path=""
    image_path=$(apptainer exec --cleanenv --userns --containall \
        --no-mount cwd,home,hostfs "$NODE_IMAGE" printenv PYTHONPATH \
        2>/dev/null || true)
    export APPTAINERENV_PYTHONPATH="/opt/scientist${image_path:+:$image_path}"
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
    # Relay hygiene (NODE_TEMPLATE = a prior run's delivered world): the
    # copy ships the prior run's conclusion and session wire. Both must
    # move aside — the snapshot loop reads a delivered conclusion.json
    # as this run's exit signal (it would stop at t=0), and the CLI
    # resumes the prior conversation whenever a wire exists with no
    # conclusion (relay semantics is a FRESH PI inheriting the world:
    # git history, notes, research memory — not the prior PI's
    # continuation). Preserved, never deleted: the records ride along.
    if [ -f "$RUN_DIR/world/.scientist/conclusion.json" ]; then
        mv "$RUN_DIR/world/.scientist/conclusion.json" \
           "$RUN_DIR/world/.scientist/conclusion.$(date +%m%d-%H%M%S).relay-prior.json"
        echo "relay: prior conclusion moved aside (*.relay-prior.json)"
    fi
    if [ -d "$RUN_DIR/world/.scientist/session" ]; then
        mv "$RUN_DIR/world/.scientist/session" \
           "$RUN_DIR/world/.scientist/session.$(date +%m%d-%H%M%S).prior"
        echo "relay: prior session moved aside (*.prior) — fresh PI, inherited records"
    fi
    # The harness body (.scientist) lives in the world but is invisible
    # to git workflows — hygiene line written at prepare time so `git
    # status` stays clean and `stash -u` never sweeps it. Hygiene, not
    # law: the enforcement point is the read-only dual-bind (see
    # docs/design/世界三区设计.md §3.1).
    touch "$RUN_DIR/world/.gitignore"
    grep -qx '\.scientist/' "$RUN_DIR/world/.gitignore" \
        || printf '.scientist/\n' >> "$RUN_DIR/world/.gitignore"
    # commit the hygiene line so the world is born with a clean
    # `git status` — agents should never wonder about it (BASE_SHA is
    # taken after this commit; relay templates just gain one commit).
    git -C "$RUN_DIR/world" commit -q -m \
        "world prepare: keep the harness body (.scientist) invisible to git" \
        -- .gitignore 2>/dev/null || true
    local p
    for p in $WORLD_RW; do
        # bind sources must exist; .gitignore is a file, not a dir —
        # its bind is how the bench config stays researcher-owned
        # (three-zone design: .gitignore rides with the research
        # surface, not the frozen zone).
        [ -e "$RUN_DIR/world/$p" ] || mkdir -p "$RUN_DIR/world/$p"
    done
    mkdir -p "$RUN_DIR/world/.scientist"  # the body: /state bind source
    git_name=$(git config --global user.name || echo wujxy)
    git_mail=$(git config --global user.email || echo "wujxy@st.usst.edu.cn")
    printf '[user]\n\tname = %s\n\temail = %s\n' "$git_name" "$git_mail" \
        > "$RUN_DIR/home/.gitconfig"
    chmod 700 "$RUN_DIR/home"
}
