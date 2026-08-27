#!/usr/bin/env bash
# Container smoke suite for the single-scientist framework (S0-S9).
# Every check prints PASS/FAIL; any FAIL aborts the arm (fail-closed —
# the launcher runs this BEFORE the detached exec). Log: $RUN_DIR/smoke.log
# Usage: bash scripts/smoke_container.sh RUN_DIR BASE_SHA
set -uo pipefail
RUN_DIR=$1
BASE_SHA=$2
REPO_ROOT=/datafs/users/wujxy/agent-sci/omilrec_opt/v1.0/SimpleEvolution
cd "$REPO_ROOT"
source scripts/_container_common.sh
LOG="$RUN_DIR/smoke.log"
: > "$LOG"

note() { printf '%s %s\n' "$1" "$2" | tee -a "$LOG"; }
check() {  # check <name> <shell-snippet ran via scientist_container>
    local name=$1 snippet=$2
    if scientist_container bash -c "$snippet" >> "$LOG" 2>&1; then
        note "PASS" "$name"
    else
        note "FAIL" "$name"
        exit 1
    fi
}
check_inv() {  # informational — never fails the suite
    local name=$1 snippet=$2
    if scientist_container bash -c "$snippet" >> "$LOG" 2>&1; then
        note "PASS" "$name (informational)"
    else
        note "NOTE" "$name (informational) — not gating"
    fi
}

# S0 toolchain present (python must be 3.9.x — the package is stdlib-only
# for exactly that image)
check S0-toolchain 'command -v python3 claude node git gcc make taskset bash && python3 -V | grep -q "Python 3\.9\."'

# S1 the frozen scientist package imports on the image python
check S1-import 'python3 -c "import scientist.cli as c; assert c.PROMPT_VERSION == \"oneworld-v5\"; print(\"IMPORT OK\", c.PROMPT_VERSION)"'

# S2 mounts visible and scratch writable
check S2-mounts '[ -d /work/src ] && [ -f /spec.json ] && [ -d /repo/scripts ] && [ -d /scratch ] && [ -w /scratch ] && [ -x /work/scripts/bench.sh ]'

# S3 EROFS sentinels — the frozen surface refuses writes, the editable
# overlay accepts them ("Edit only src" is physical, not prose)
check S3-erofs-frozen-side 'for p in /work/scripts/.smoke /work/README.md /work/benchmarks/.smoke; do if : >> "$p" 2>/dev/null; then echo "UNEXPECTED WRITE SUCCEEDED: $p"; exit 1; fi; done; echo erofs-ok'
check S3-erofs-editable-side ': > /work/src/.smoke_rw && rm /work/src/.smoke_rw'

# S4 git works read-only AND has a committer identity (the per-run
# .gitconfig in the HOME bind)
check S4-git '[ "$(git -C /work rev-parse HEAD)" = "'"$BASE_SHA"'" ] && git -C /work log --oneline -1 | grep -q . && git -C /work var GIT_COMMITTER_IDENT | grep -q "@"'

# S5 model transport from inside the container: one real completion via
# the exact code path the scientist CLI uses (model_stdlib — pure stdlib,
# the SDK path needs a package the image deliberately does not carry)
check S5-model 'python3 - <<"PY"
import json
from scientist.model_stdlib import build_stdlib_chat_model
spec = json.load(open("/spec.json"))
model = build_stdlib_chat_model(dict(spec["model"]))
reply = model.complete(
    system="You are a smoke test.", timeout_seconds=120,
    json_object=False,
    messages=[{"role": "user", "content": "Reply with the single word OK"}],
)
assert reply.text and reply.text.strip(), "empty model reply"
print("MODEL OK", reply.text.strip()[:20])
PY'

# S6 assistant claude inside the container, with the spec credentials and
# an isolated config dir (no host ~/.claude dependency). creds.sh lives in
# the container's ephemeral /tmp and is removed immediately.
check S6-assistant-claude '
python3 - > /tmp/creds.sh <<"PY"
import json
e = json.load(open("/spec.json"))["assistant"]["env"]
print("export ANTHROPIC_AUTH_TOKEN=" + e["ANTHROPIC_AUTH_TOKEN"])
print("export ANTHROPIC_BASE_URL=" + e["ANTHROPIC_BASE_URL"])
PY
. /tmp/creds.sh && rm -f /tmp/creds.sh
out=$(claude -p --output-format stream-json --verbose "Reply with exactly OK" 2>/dev/null | tail -1)
echo "$out" | grep -q "\"type\":\"result\"" && echo "$out" | grep -q "OK"'

# S7 the full bench flow under the mount layout (build lands in the src
# overlay; gcc/make/taskset/nproc all inside)
check S7-bench 'bash /work/scripts/check_verify.sh 2>&1 | grep -q "verify: PASS" && bash /work/scripts/bench.sh 2>&1 | grep -E "lookups_per_sec=[0-9]" | grep -q . && echo BENCH-OK'

# S8 /tmp is writable and run-local (informational)
check_inv S8-tmp ': > /tmp/.smoke && rm /tmp/.smoke && echo tmp-ok'

# S9 isolation sentinels — no host paths visible from inside
check S9-isolation '[ ! -e /datafs ] && [ ! -e '"$REPO_ROOT"' ] && [ ! -e /root/runs ] && ! ls / | grep -qE "^(datafs|lustrefs|cvmfs)$"'

note DONE "smoke suite complete"
echo "smoke: all green -> $LOG"
