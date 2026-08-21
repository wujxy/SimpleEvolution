#!/usr/bin/env bash
# SimpleEvolution cluster forward proxy — run WITHOUT root on the jump host.
#
# JUNO HTCondor execute nodes have no external internet, so third-party model
# APIs (DeepSeek / Zhipu / Anthropic / OpenAI) must be reached through an HTTP
# CONNECT forward proxy on a jump host that DOES have direct internet (e.g.
# 192.168.237.165).  This script stands that proxy up as the CURRENT USER — no
# sudo needed — and restricts use to the source subnets in ALLOW_SUBNETS.
#
# Backends (first available wins; both enforce ALLOW_SUBNETS without root):
#   1. tinyproxy         if already installed (user-mode config, no systemd)
#   2. forward_proxy.py  the bundled stdlib-only Python proxy (no install)
#
# Usage:
#   proxy/setup_proxy.sh [start|status|restart|stop]   # default: start
#
# Env (all optional):
#   PORT            listen port          (default 3128)
#   BIND_ADDR       listen address       (default 0.0.0.0)
#   ALLOW_SUBNETS   space-separated CIDR source subnets allowed to use the
#                   proxy (default: 192.168.0.0/16 10.0.0.0/8 127.0.0.1)
#
# After it is up, verify from a condor EXECUTE node (not this host):
#   curl -x http://<this-host>:${PORT} -I https://api.deepseek.com
#
# Then point SimpleEvolution condor jobs at it under the task YAML 'jobs:' block
# (see proxy/README.md), e.g. https_proxy: http://<this-host>:${PORT}
set -euo pipefail

PORT="${PORT:-3128}"
BIND_ADDR="${BIND_ADDR:-0.0.0.0}"
ALLOW_SUBNETS="${ALLOW_SUBNETS:-192.168.0.0/16 10.0.0.0/8 127.0.0.1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${SIMPLEEVO_PROXY_DIR:-$HOME/.simpleevo-proxy}"
CONF_FILE="$RUN_DIR/proxy.conf"
PID_FILE="$RUN_DIR/proxy.pid"
LOG_FILE="$RUN_DIR/proxy.log"

die() { echo "error: $*" >&2; exit 1; }

# ------------------------------------------------------------- introspection

pick_backend() {
    if command -v tinyproxy >/dev/null 2>&1; then
        echo tinyproxy
    elif command -v python3 >/dev/null 2>&1; then
        echo python
    else
        echo none
    fi
}

probe_host() {
    case "$BIND_ADDR" in
        "" | "0.0.0.0" | "::") echo "127.0.0.1" ;;
        *) echo "$BIND_ADDR" ;;
    esac
}

is_listening() {
    local host
    host="$(probe_host)"
    # A subshell TCP connect; the fd closes when the subshell exits.
    (exec 3<>"/dev/tcp/${host}/${PORT}") >/dev/null 2>&1
}

running_pid() {
    [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# ------------------------------------------------------------------ backends

write_tinyproxy_conf() {
    # No User/Group directives: when run as a normal user tinyproxy stays as
    # the invoking user (root may add them by hand).  Everything is under
    # $RUN_DIR so no /etc or /var access is needed.
    {
        echo "Port $PORT"
        echo "Listen $BIND_ADDR"
        for net in $ALLOW_SUBNETS; do echo "Allow $net"; done
        echo "ConnectPort 80"
        echo "ConnectPort 443"
        echo "Timeout 600"
        echo "LogLevel Info"
        echo "LogFile $LOG_FILE"
        echo "PidFile $PID_FILE"
    } >"$CONF_FILE"
}

start_backend() {
    local backend
    backend="$(pick_backend)"
    mkdir -p "$RUN_DIR"
    case "$backend" in
        tinyproxy)
            write_tinyproxy_conf
            echo "==> backend: tinyproxy on ${BIND_ADDR}:${PORT}"
            tinyproxy -c "$CONF_FILE" 2>>"$LOG_FILE" || die "tinyproxy failed (see $LOG_FILE)"
            ;;
        python)
            echo "==> backend: forward_proxy.py on ${BIND_ADDR}:${PORT}"
            ALLOW_SUBNETS="$ALLOW_SUBNETS" BIND_ADDR="$BIND_ADDR" PORT="$PORT" \
                nohup python3 "$SCRIPT_DIR/forward_proxy.py" >>"$LOG_FILE" 2>&1 &
            echo $! >"$PID_FILE"
            ;;
        none)
            die "no proxy backend available: need tinyproxy or python3 on this host"
            ;;
    esac
    echo "$backend" >"$RUN_DIR/backend.txt"
    # The port accepting connections is not enough: another process may already
    # hold it, and our backend can take a beat to die after a failed bind.
    # Wait for the listener, then require OUR backend to be alive (tinyproxy
    # writes $PID_FILE, the python backend gets it from $!).
    if ! wait_for_listen; then
        echo "error: proxy did not come up on ${BIND_ADDR}:${PORT} (see $LOG_FILE)" >&2
        rm -f "$PID_FILE" "$RUN_DIR/backend.txt"
        return 1
    fi
    sleep 1  # let an immediate bind/start failure surface
    if ! running_pid; then
        echo "error: proxy exited right after start — is ${BIND_ADDR}:${PORT} in use by another process?" >&2
        echo "       (see $LOG_FILE)" >&2
        rm -f "$PID_FILE" "$RUN_DIR/backend.txt"
        return 1
    fi
}

wait_for_listen() {
    local i
    for i in $(seq 1 20); do
        if is_listening; then return 0; fi
        sleep 0.5
    done
    return 1
}

stop_backend() {
    if [ -s "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            local i
            for i in $(seq 1 20); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.2
            done
            kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
    [ -s "$RUN_DIR/backend.txt" ] && rm -f "$RUN_DIR/backend.txt"
}

# ------------------------------------------------------------------- actions

do_start() {
    if running_pid; then
        echo "==> proxy already running (pid $(cat "$PID_FILE")); nothing to do"
        do_status
        return 0
    fi
    start_backend
    echo "==> proxy listening on ${BIND_ADDR}:${PORT} (allowed: $ALLOW_SUBNETS)"
    warn_firewall
    hint_cron
    print_verify
}

do_stop() {
    if ! running_pid; then
        echo "==> proxy not running (no live pid in $PID_FILE)"
        return 0
    fi
    echo "==> stopping proxy (pid $(cat "$PID_FILE"))"
    stop_backend
    echo "==> proxy stopped"
}

do_status() {
    echo "backend:    $(cat "$RUN_DIR/backend.txt" 2>/dev/null || echo none)"
    echo "listen:     ${BIND_ADDR}:${PORT}"
    echo "allowed:    $ALLOW_SUBNETS"
    if running_pid; then
        echo "status:     RUNNING (pid $(cat "$PID_FILE"))"
    else
        echo "status:     stopped"
    fi
    if is_listening; then
        echo "port ${PORT}: accepting connections"
    else
        echo "port ${PORT}: not accepting connections"
    fi
}

warn_firewall() {
    if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        echo
        echo "note: firewalld is active — if execute nodes cannot reach port ${PORT},"
        echo "      an admin (you have no sudo on this account) must open it:"
        echo "        sudo firewall-cmd --add-port=${PORT}/tcp --permanent && sudo firewall-cmd --reload"
    fi
}

hint_cron() {
    echo
    echo "This account has no systemd; to auto-start after a reboot:"
    if crontab -l >/dev/null 2>&1; then
        echo "  crontab -l | { cat; echo '@reboot ${SCRIPT_DIR}/setup_proxy.sh start'; } | crontab -"
    else
        echo "  crontab is denied for this account (PAM) — no @reboot entry can be installed."
        echo "  Instead, add the idempotent start line to ~/.profile so the proxy comes back"
        echo "  up on your first login after a reboot (still needs one login; fully-unattended"
        echo "  boot-start would need an admin's systemd unit). Run it FOREGROUND (no '&') so"
        echo "  the launch reliably completes; the only cost is a ~1s login delay when down:"
        echo "    pgrep -f '[f]orward_proxy.py' >/dev/null 2>&1 || ${SCRIPT_DIR}/setup_proxy.sh start >>\$HOME/.simpleevo-proxy/start.log 2>&1"
    fi
}

print_verify() {
    local host_ip
    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo
    echo "Verify from a condor EXECUTE node (not this host):"
    echo "  curl -x http://${host_ip:-<this-host>}:${PORT} -I https://api.deepseek.com"
    echo
    echo "Then set it in the task YAML 'jobs:' block (see proxy/README.md):"
    echo "  jobs:"
    echo "    backend: condor"
    echo "    https_proxy: http://${host_ip:-<this-host>}:${PORT}"
    echo "    http_proxy:  http://${host_ip:-<this-host>}:${PORT}"
    echo "    no_proxy: localhost,127.0.0.1,aiapi.ihep.ac.cn"
}

# ------------------------------------------------------------------- dispatch

action="${1:-start}"
case "$action" in
    start)  do_start ;;
    stop)   do_stop ;;
    status) do_status ;;
    restart) do_stop; do_start ;;
    *) die "unknown action '$action' (start|status|restart|stop)" ;;
esac
