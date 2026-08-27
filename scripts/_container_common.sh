#!/usr/bin/env bash
# Shared container argv for the single-scientist container test framework.
# Sourced by run_xsbench_3h_container.sh and smoke_container.sh; both must
# set RUN_DIR and REPO_ROOT before sourcing. One container = one world:
# mounts ARE the boundary (入世 constitution, docs/design/scientist工具分类与入世设计.md).
#
# Write layout (deliberate deviation from "only editable paths accept
# writes"): /work is a ro base with THREE rw overlays — src/ (the editable
# set), .scientist/ and .git/ (harness plumbing: ledger, session,
# collaborator artifacts, the PI's own commits). The scientifically frozen
# surface (scripts/, benchmarks/, README) is EROFS by mount.
scientist_container() {
    apptainer exec --cleanenv --no-eval --userns --containall \
        --no-mount cwd,home,hostfs --cwd /work \
        --bind "$RUN_DIR/world:/work:ro" \
        --bind "$RUN_DIR/world/src:/work/src:rw" \
        --bind "$RUN_DIR/world/.scientist:/work/.scientist:rw" \
        --bind "$RUN_DIR/world/.git:/work/.git:rw" \
        --bind "$REPO_ROOT/examples/xsbench_opt/repo:/repo:ro" \
        --bind "$RUN_DIR/scratch:/scratch:rw" \
        --bind "$RUN_DIR/spec.json:/spec.json:ro" \
        --bind "$RUN_DIR/pkg/scientist:/opt/scientist/scientist:ro" \
        --bind "$RUN_DIR/home:/home/wujxy:rw" \
        "$REPO_ROOT/examples/xsbench_opt/apptainer.sif" "$@"
}

# Env injection for the exec (call AFTER the nested-container hygiene
# unsets — order matters). Credentials deliberately do NOT go through
# here: the model key rides in spec.json (model_stdlib reads the spec),
# and the assistant claude's ANTHROPIC_* arrive via spec.assistant.env
# merged over os.environ in _spawn. Never echo these.
container_env_exports() {
    export APPTAINERENV_PYTHONPATH=/opt/scientist
    export APPTAINERENV_PYTHONPYCACHEPREFIX=/scratch/pycache
    export APPTAINERENV_CLAUDE_CONFIG_DIR=/scratch/claude-config
    [ -n "${BENCH_PIN:-}" ] && export APPTAINERENV_BENCH_PIN="$BENCH_PIN"
    return 0
}
