#!/usr/bin/env bash
# singlenode smoke suite (S0-S9): every check PASS/FAIL, any FAIL aborts
# (fail-closed — launchers run this before detaching). Mode-agnostic: the
# scientist-specific S1 is skipped when the frozen package is absent.
# Usage: bash singlenode/smoke.sh RUN_DIR BASE_SHA
set -uo pipefail
RUN_DIR=$1
BASE_SHA=$2
source "$(dirname "$0")/node_common.sh"
LOG="$RUN_DIR/smoke.log"
: > "$LOG"

note() { printf '%s %s\n' "$1" "$2" | tee -a "$LOG"; }
check() {
    local name=$1 snippet=$2
    if node_container bash -c "$snippet" >> "$LOG" 2>&1; then
        note "PASS" "$name"
    else
        note "FAIL" "$name"
        exit 1
    fi
}
check_inv() {
    local name=$1 snippet=$2
    if node_container bash -c "$snippet" >> "$LOG" 2>&1; then
        note "PASS" "$name (informational)"
    else
        note "NOTE" "$name (informational) — not gating"
    fi
}

check S0-toolchain 'command -v python3 claude node git gcc make taskset bash && python3 -V | grep -q "Python 3\.9\."'
if [ -d "$RUN_DIR/pkg/scientist" ]; then
    check S1-import 'python3 -c "import scientist.cli; print(\"IMPORT OK\", scientist.cli.PROMPT_VERSION)"'
else
    note "SKIP" "S1-import (no frozen scientist package — coding mode)"
fi
# S2/S3 are layout checks; every snippet below is the xsbench/jrb default
# and a task launcher with a different shape overrides the env var.
S2_CHECK=${S2_CHECK:-'[ -d /work/src ] && [ -f /spec.json ] && [ -d /repo/scripts ] && [ -d /scratch ] && [ -w /scratch ] && [ -x /work/scripts/bench.sh ]'}
S3_FROZEN=${S3_FROZEN:-'/work/scripts/.smoke /work/README.md /work/benchmarks/.smoke'}
S3_EDITABLE_RW=${S3_EDITABLE_RW:-'/work/src'}
check S2-mounts "$S2_CHECK"
check S3-erofs-frozen-side 'for p in '"$S3_FROZEN"'; do if : >> "$p" 2>/dev/null; then echo "UNEXPECTED WRITE SUCCEEDED: $p"; exit 1; fi; done; echo erofs-ok'
check S3-erofs-editable-side 'for p in '"$S3_EDITABLE_RW"'; do : > "$p/.smoke_rw" && rm "$p/.smoke_rw"; done; echo rw-ok'
# S3b — the harness body (three-zone world §3.1): read-only to every
# actor, invisible to git, writable only through /state; and the blessed
# clean-baseline path (git worktree) must function. This is the gate
# form of the 2026-08-30 incident: a legitimate `git stash -u` must not
# be able to take the body, and no accidental write may land on it.
check S3b-body '
  if : >> /work/.scientist/.smoke_probe 2>/dev/null; then
      echo "BODY WRITE SUCCEEDED (expected read-only)"; exit 1; fi;
  if git -C /work status --porcelain 2>/dev/null | grep -q "\.scientist"; then
      echo "BODY VISIBLE TO GIT"; exit 1; fi;
  if ! (: > /state/.smoke_probe && rm /state/.smoke_probe) 2>/dev/null; then
      echo "STATE NOT WRITABLE"; exit 1; fi;
  if ! (git -C /work worktree add /scratch/.smoke-wt HEAD >/dev/null 2>&1 \
        && git -C /work worktree remove /scratch/.smoke-wt >/dev/null 2>&1); then
      echo "WORKTREE ADD FAILED"; exit 1; fi;
  echo body-ok'
check S4-git '[ "$(git -C /work rev-parse HEAD)" = "'"$BASE_SHA"'" ] && git -C /work log --oneline -1 | grep -q . && git -C /work var GIT_COMMITTER_IDENT | grep -q "@"'
check S5-model 'python3 - <<"PY"
import json, urllib.request
spec = json.load(open("/spec.json"))
m = spec["model"]
req = urllib.request.Request(
    m["base_url"].rstrip("/") + "/chat/completions",
    data=json.dumps({"model": m["model"], "max_tokens": 8,
                     "messages": [{"role": "user",
                                   "content": "Reply with the single word OK"}]}).encode(),
    headers={"content-type": "application/json",
             "authorization": "Bearer " + m["api_key"]})
with urllib.request.urlopen(req, timeout=120) as r:
    assert r.status == 200, "HTTP %s" % r.status
    body = json.loads(r.read())
assert body.get("choices"), "no choices in reply"
print("MODEL OK transport+auth")
PY'
check S6-assistant-claude '
python3 - > /tmp/creds.sh <<"PY"
import json
e = json.load(open("/spec.json")).get("assistant", {}).get("env", {})
if not e:
    raise SystemExit("no assistant.env in spec — coding mode should pass creds via env")
print("export ANTHROPIC_AUTH_TOKEN=" + e["ANTHROPIC_AUTH_TOKEN"])
print("export ANTHROPIC_BASE_URL=" + e["ANTHROPIC_BASE_URL"])
PY
. /tmp/creds.sh && rm -f /tmp/creds.sh
out=$(claude -p --output-format stream-json --verbose "Reply with exactly OK" 2>/dev/null | tail -1)
echo "$out" | grep -q "\"type\":\"result\"" && echo "$out" | grep -q "OK"'
# S7 is benchmark-specific (the one check that knows the metric names):
# S7_BENCH_CMD overrides the default xsbench command for other benchmarks.
S7_DEFAULT='bash /work/scripts/check_verify.sh 2>&1 | grep -q "verify: PASS" && bash /work/scripts/bench.sh 2>&1 | grep -E "lookups_per_sec=[0-9]" | grep -q . && echo BENCH-OK'
check S7-bench "${S7_BENCH_CMD:-$S7_DEFAULT}"
check_inv S8-tmp ': > /tmp/.smoke && rm /tmp/.smoke && echo tmp-ok'
# /cvmfs is forbidden by default (nothing in the jrb/xsbench worlds needs
# it); a task that binds it explicitly (the JUNO toolchain) has declared
# it, so it drops out of the forbidden set — the rest of the isolation
# contract (no host repo, no runs, no datafs) never relaxes.
S9_FORBIDDEN='^(datafs|lustrefs|cvmfs)$'
case " $EXTRA_RO_BINDS " in
    *" /cvmfs "*) S9_FORBIDDEN='^(datafs|lustrefs)$' ;;
esac
check S9-isolation '[ ! -e /datafs ] && [ ! -e '"$REPO_ROOT"' ] && [ ! -e /root/runs ] && ! ls / | grep -qE "'"$S9_FORBIDDEN"'"'

note DONE "smoke suite complete"
echo "smoke: all green -> $LOG"
