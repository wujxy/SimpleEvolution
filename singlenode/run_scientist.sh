#!/usr/bin/env bash
# singlenode — scientist mode: the ENTIRE scientist run executes inside
# ONE apptainer container (mounts are the boundary; the host only opens
# the world and collects it). See node_common.sh for mount semantics.
#
# Usage:   bash singlenode/run_scientist.sh [RUN_DIR]
# Env:     NODE_IMAGE / NODE_TEMPLATE / SPEC_TEMPLATE (defaults: xsbench)
#          BENCH_PIN (optional core pin), FORCE=1, SMOKE_ONLY=1, WALL=seconds
# After:   python scripts/replay_xsbench.py \
#              --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
set -euo pipefail
source "$(dirname "$0")/node_common.sh"
cd "$REPO_ROOT"

RUN_DIR=${1:-runs/singlenode/scientist-$(date +%m%d-%H%M)}
WALL=${WALL:-10800}
PY=${PY:-/datafs/users/wujxy/py_venv/my_env/bin/python}

node_unset_inherited_binds

# RESUME=1 — the retry side of the persistence contract: re-enter a run
# whose agent died on infra (the agent exits 1 with a crashed conclusion;
# wire.jsonl is the single source of truth). Keeps world/wire/spec/pkg/
# snapshots, skips every one-time step (template copy would nest inside
# the existing world, pkg re-copy would nest inside pkg/), skips smoke
# (this exact world already passed). The crashed conclusion is moved
# aside — the snapshot loop watches conclusion.json as the exit signal,
# and the new attempt writes its own when it concludes.
RESUMING=0
if [ -e "$RUN_DIR/run.log" ] || [ -d "$RUN_DIR/world" ]; then
    if [ "${RESUME:-0}" = "1" ] && [ -f "$RUN_DIR/world/.scientist/session/wire.jsonl" ]; then
        RESUMING=1
        echo "RESUME=1 — reusing world/wire/spec/pkg in $RUN_DIR"
        if [ -f "$RUN_DIR/world/.scientist/conclusion.json" ]; then
            mv "$RUN_DIR/world/.scientist/conclusion.json" \
               "$RUN_DIR/world/.scientist/conclusion.$(date +%m%d-%H%M%S).crashed.json"
            echo "crashed conclusion moved aside (preserved as *.crashed.json)"
        fi
    elif [ "${FORCE:-0}" != "1" ]; then
        echo "refusing to overwrite existing run at $RUN_DIR (FORCE=1 to override, RESUME=1 to re-enter after infra death)" >&2
        exit 1
    else
        rm -rf "$RUN_DIR/world" "$RUN_DIR/snapshots" "$RUN_DIR/run.log" "$RUN_DIR/pkg"
    fi
fi

# No environment exports for the run: credentials ride the spec (read
# from the file below, never echoed, never ambient), and model routing
# is a declared spec value — an exported ANTHROPIC_* block once parked
# three dead MODEL vars in the process env while the seat model fell to
# the CLI's own resolution (the v4-pro accident, r8/r9).

if [ "$RESUMING" = 0 ]; then
    node_prepare_run_dir
fi
BASE_SHA=$(git -C "$RUN_DIR/world" rev-parse HEAD)

if [ "$RESUMING" = 0 ]; then
    # spec assembly is world-layer knowledge (credential layout lives
    # here, beside node_common.sh); this script only launches
    $PY singlenode/specfill.py \
        "$SPEC_TEMPLATE" "$RUN_DIR/spec.json" "$BASE_SHA"
    chmod 600 "$RUN_DIR/spec.json"

    # freeze the scientist package for this run (upgrade = re-copy)
    cp -a "$REPO_ROOT/scientist" "$RUN_DIR/pkg/scientist"
fi

# Task-specific one-time setup once the run dir, spec, and frozen package
# all exist (e.g. the omilrec world pip-installs pytest into the run's
# home for the cvmfs python its eval uses). Eval'd with node_container
# available; empty for jrb/xsbench. One-time by contract: skipped on
# RESUME (already applied to this world).
if [ "$RESUMING" = 0 ] && [ -n "${POST_PREPARE_HOOK:-}" ]; then
    eval "$POST_PREPARE_HOOK"
fi

node_scientist_env

# --state reaches only CLIs that know it: a run frozen on older code (the
# v6 package of an old-world control arm) predates the flag and would die
# on argparse. Probe the frozen package, not the repo tree.
if grep -q -- '--state' "$RUN_DIR/pkg/scientist/cli.py" 2>/dev/null; then
    STATE_ARGS="--state /state"
else
    STATE_ARGS=""
fi
export STATE_ARGS

# smoke gate (fail-closed), then optional stop. Skipped on RESUME: this
# exact world already passed it once.
if [ "$RESUMING" = 0 ]; then
    bash "$SINGLENODE_DIR/smoke.sh" "$RUN_DIR" "$BASE_SHA"
    if [ "${SMOKE_ONLY:-0}" = "1" ]; then
        echo "SMOKE_ONLY=1 — stopping after smoke. run_dir=$RUN_DIR"
        exit 0
    fi
fi

# pre-flight probe: one model call through the real mounted world
node_container python3 -m scientist.cli \
    --spec /spec.json --world /work --repo /repo --scratch /scratch $STATE_ARGS --probe

# detached run, SUPERVISED (the persistence contract's scheduler side):
# on infra death (transport/model failure) the agent exits 1 with a
# crashed conclusion; the supervisor moves that conclusion aside (cli
# resumes only in its absence) and relaunches — the conversation
# rebuilds from wire.jsonl. Non-infra crashes and unreadable exits are
# left loudly for humans (never mask a real bug in a restart loop);
# 5 consecutive quick crashes give up; a healthy attempt that ran
# ≥10 min resets the crashloop counter. WALL is honored globally here —
# each cli attempt sees a fresh spec wall, so this loop is the one
# place the launch wall caps the whole span end-to-end.
export RUN_DIR PY WALL
setsid nohup bash -c '
    source "$0/node_common.sh"
    deadline=$(( $(date +%s) + ${WALL:-604800} ))
    attempts=0
    while [ "$(date +%s)" -lt "$deadline" ]; do
        attempt_start=$(date +%s)
        node_container python3 -m scientist.cli --spec /spec.json --world /work --repo /repo --scratch /scratch $STATE_ARGS
        rc=$?
        # Orphan sweep (three-zone world §3.3): seats are setsid-detached
        # inside the container pid namespace, so a dead agent can leave
        # live seats mutating the world (observed: a 33-minute orphan whose
        # parting git stash ate the harness body). proc.pid records are
        # namespace pids — unusable host-side. Instead: every process whose
        # mountinfo names THIS world bind is inside this container
        # namespace; once the agent has exited, whatever remains there is
        # an orphan. TERM, breathe, KILL.
        # (quote discipline: the body of this -c script must contain zero
        # single-quote characters — one such pair in a comment here once
        # silently regrouped the command words and the supervisor never
        # started. Glob, no grep.)
        # Match by run basename, not the launch-path form: mountinfo
        # records bind sources with the /datafs mount prefix stripped
        # (/users/...), so an absolute launch path would never match its
        # own string and the sweep would silently no-op. Run dir names
        # are unique by convention.
        world_pat="/$(basename "$RUN_DIR")/world"
        for proc in /proc/[0-9]*; do
            grep -qF "$world_pat" "$proc/mountinfo" 2>/dev/null \
                || continue
            pid=${proc#/proc/}
            echo "[supervisor] orphan sweep: TERM pid $pid"
            kill -TERM -- "-$pid" 2>/dev/null \
                || kill -TERM "$pid" 2>/dev/null || true
        done
        sleep 3
        for proc in /proc/[0-9]*; do
            grep -qF "$world_pat" "$proc/mountinfo" 2>/dev/null \
                || continue
            pid=${proc#/proc/}
            kill -KILL -- "-$pid" 2>/dev/null \
                || kill -KILL "$pid" 2>/dev/null || true
        done
        [ "$rc" -eq 0 ] && exit 0
        if [ $(( $(date +%s) - attempt_start )) -ge 600 ]; then
            attempts=0   # ran healthy ≥10 min: not a crashloop
        fi
        attempts=$((attempts + 1))
        conc="$RUN_DIR/world/.scientist/conclusion.json"
        reason=$("$PY" -c "import json;print(json.load(open(\"$conc\"))[\"conclusion\"][\"reason\"])" 2>/dev/null || echo unreadable)
        case "$reason" in
            "model failure:"*) ;;
            *)
                echo "[supervisor] agent exited rc=$rc, crash not infra-class (reason: $reason) — leaving it for humans"
                exit 1
                ;;
        esac
        if [ "$attempts" -ge 5 ]; then
            echo "[supervisor] giving up after $attempts quick crashes — crashed conclusion left in place"
            exit 1
        fi
        mv "$conc" "$RUN_DIR/world/.scientist/conclusion.$(date +%m%d-%H%M%S).attempt$attempts.crashed.json"
        delay=$((60 * attempts))
        echo "[supervisor] infra crash (attempt $attempts, rc=$rc) — resuming from wire in ${delay}s"
        sleep "$delay"
    done
    echo "[supervisor] launch WALL reached — stopping"
' "$SINGLENODE_DIR" < /dev/null >> "$RUN_DIR/run.log" 2>&1 &
AGENT=$!
setsid nohup "$PY" "$REPO_ROOT/scripts/snapshot_world_loop.py" \
    --world "$RUN_DIR/world" --out "$RUN_DIR/snapshots" \
    --subdir "${SNAPSHOT_SUBDIR:-src}" \
    --every 60 --max-seconds "$WALL" \
    >> "$RUN_DIR/snapshot.log" 2>&1 &
SNAP=$!
echo "run_dir=$RUN_DIR agent_pid=$AGENT snapshot_pid=$SNAP wall=${WALL}s base_sha=$BASE_SHA"
echo "stop with: kill -TERM -- -$AGENT"
echo "tail: tail -f $RUN_DIR/run.log"
