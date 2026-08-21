#!/usr/bin/env bash
# One-time setup of the SimpleEvolution cluster forward proxy.
#
# Run this ON the jump host (e.g. 192.168.237.165 — a machine on the IHEP
# network with DIRECT outbound internet). It installs an HTTP CONNECT forward
# proxy so that HTCondor execute nodes (which have no external internet) can
# reach third-party model APIs through this host.
#
# Two backends, in order of preference:
#   1. tinyproxy   (systemd daemon; needs root; port 3128)
#   2. pproxy      (user-space, no root; `pip install pproxy`; port >=1024)
#
# Usage:
#   sudo ALLOW_SUBNETS="192.168.0.0/16 10.0.0.0/8" ./proxy/setup_proxy.sh
#
# After it is up, verify from an EXECUTE node (not this host):
#   curl -x http://192.168.237.165:3128 -I https://api.deepseek.com
set -euo pipefail

PORT="${PORT:-3128}"
# Subnets allowed to USE the proxy. MUST be narrowed to the JUNO execute-node
# subnets — do not leave this open or the host becomes an open relay.
ALLOW_SUBNETS="${ALLOW_SUBNETS:-192.168.0.0/16 10.0.0.0/8 127.0.0.1}"
BIND_ADDR="${BIND_ADDR:-0.0.0.0}"

die() { echo "error: $*" >&2; exit 1; }

if command -v tinyproxy >/dev/null 2>&1 || [ "$(id -u)" = 0 ]; then
    echo "==> Installing tinyproxy"
    if ! command -v tinyproxy >/dev/null 2>&1; then
        yum install -y tinyproxy || apt-get install -y tinyproxy \
            || die "could not install tinyproxy (try the pproxy path below)"
    fi

    CONF=/etc/tinyproxy/tinyproxy.conf
    echo "==> Writing $CONF"
    cp -a "$CONF" "$CONF.bak.$(date +%s)" 2>/dev/null || true
    {
        echo "User nobody"
        echo "Group nogroup"
        echo "Port $PORT"
        echo "Listen $BIND_ADDR"
        for net in $ALLOW_SUBNETS; do echo "Allow $net"; done
        echo "ConnectPort 443"
        echo "ConnectPort 80"
        echo "Timeout 600"
        echo "LogLevel Info"
        echo "LogFile /var/log/tinyproxy/tinyproxy.log"
    } > "$CONF"

    systemctl enable --now tinyproxy 2>/dev/null \
        || systemctl restart tinyproxy 2>/dev/null \
        || die "tinyproxy installed but systemd failed to start it"
    echo "==> tinyproxy listening on ${BIND_ADDR}:${PORT}"
else
    echo "==> No root / no tinyproxy; falling back to pproxy"
    python3 -m pproxy --version >/dev/null 2>&1 \
        || pip install --user pproxy || pip install pproxy \
        || die "pproxy not available; install it with: pip install pproxy"
    # Launch in the background and keep it across logins via nohup.
    nohup python3 -m pproxy -l "http://${BIND_ADDR}:${PORT}" \
        >> /tmp/pproxy.log 2>&1 &
    sleep 1
    echo "==> pproxy listening on ${BIND_ADDR}:${PORT} (pid $!; log /tmp/pproxy.log)"
fi

echo
echo "Verify from a condor EXECUTE node:"
echo "  curl -x http://192.168.237.165:${PORT} -I https://api.deepseek.com"
echo
echo "Then point SimpleEvolution condor jobs at it (task YAML 'jobs' block):"
echo "  jobs:"
echo "    backend: condor"
echo "    https_proxy: http://192.168.237.165:${PORT}"
echo "    # keep internal endpoints off the proxy:"
echo "    no_proxy: localhost,127.0.0.1,aiapi.ihep.ac.cn"
