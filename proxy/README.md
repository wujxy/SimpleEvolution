# Cluster forward proxy for external model APIs

JUNO HTCondor execute nodes have **no external internet**, so any model
provider reached over the public internet (DeepSeek, Zhipu, Anthropic, OpenAI,
…) times out from worker nodes. The internal gateway `aiapi.ihep.ac.cn` works
around this only for the models IHEP serves.

To use **any** third-party provider from condor jobs, run an HTTP CONNECT
forward proxy on a jump host that does have direct outbound internet (e.g.
`192.168.237.165`), then point SimpleEvolution condor jobs at it.

The proxy runs **as the current user — no sudo required**, which is what a
plain `wujxy` account on the jump host gets you. It binds port 3128 (≥1024,
so no root is needed) and is restricted to the source subnets in
`ALLOW_SUBNETS`, so the host does not become an open relay.

## 1. Stand up the proxy on the jump host

On `192.168.237.165` (the host with direct internet), from the repo root:

```bash
ALLOW_SUBNETS="192.168.0.0/16 10.0.0.0/8" ./proxy/setup_proxy.sh start
```

Backends, first available wins — both enforce `ALLOW_SUBNETS` without root:

| backend | when | how |
| --- | --- | --- |
| `tinyproxy` | already installed | user-mode config under `~/.simpleevo-proxy/`; `Allow <subnet>` lines; daemonizes with its own pid file |
| `forward_proxy.py` | fallback (always available) | bundled stdlib-only Python proxy (see below); native source-IP ACL |

There is **no `pip install`** step: the Python fallback uses only `python3`
standard library, so it works even when PyPI is blocked (a host allowed
outbound to `api.deepseek.com` may still block `pypi.org`).

### Lifecycle (no systemd)

The script manages the daemon itself; `start` is the default action:

```bash
./proxy/setup_proxy.sh start     # launch
./proxy/setup_proxy.sh status    # backend / pid / port / ACL
./proxy/setup_proxy.sh restart
./proxy/setup_proxy.sh stop
```

State lives in `~/.simpleevo-proxy/` (`proxy.conf`, `proxy.log`, `proxy.pid`,
`backend.txt`). To survive a reboot of the jump host, add an `@reboot` crontab
entry (the script prints the exact line; `crontab` needs no sudo):

```text
@reboot /path/to/SimpleEvolution/proxy/setup_proxy.sh start
```

> **Firewall**: if `firewalld` is active and blocks inbound 3128, only an
> admin (sudo) can open it — the script prints the exact `firewall-cmd` line
> and warns at `start`. Verify from an execute node (step below) to catch this.

### Configuration

| env | default | meaning |
| --- | --- | --- |
| `PORT` | `3128` | listen port (≥1024, no root needed) |
| `BIND_ADDR` | `0.0.0.0` | listen interface |
| `ALLOW_SUBNETS` | `192.168.0.0/16 10.0.0.0/8 127.0.0.1` | space-separated CIDR source subnets allowed to use the proxy — narrow to the execute-node subnets |

Keep `ALLOW_SUBNETS` narrowed: both backends refuse connections from any other
source, and CONNECT is restricted to ports 80/443.

### Verify from a condor **execute node** (not the jump host)

```bash
curl -x http://192.168.237.165:3128 -I https://api.deepseek.com
```

## 2. Point SimpleEvolution condor jobs at it

Add the proxy under the task YAML `jobs:` block. It is written into
`run_dir/job_env.sh`, which every condor job sources; the proposer's SDKs and
the executor's `claude` CLI both honour these env vars, and they are forwarded
into the Apptainer containers automatically.

```yaml
jobs:
  backend: condor
  # ... existing collector/schedd/accounting settings ...
  https_proxy: http://192.168.237.165:3128
  http_proxy:  http://192.168.237.165:3128
  # Keep internal endpoints off the proxy; add any other in-network services.
  no_proxy: localhost,127.0.0.1,aiapi.ihep.ac.cn
```

Notes:

- `http_proxy` / `https_proxy` are emitted as both upper- and lower-case env
  vars (`HTTP_PROXY`/`http_proxy`, `HTTPS_PROXY`/`https_proxy`).
- When any proxy is configured and `no_proxy` is omitted, it defaults to
  `localhost,127.0.0.1`. If a task mixes internal endpoints (e.g. the HEPAI
  gateway) with external providers, list the internal hostnames in `no_proxy`.
- Unset fields leave the current behaviour unchanged (the submit host's own
  proxy env is forwarded as before). The configured proxy is authoritative for
  condor jobs only — the local backend is unaffected.

## The Python fallback proxy (`forward_proxy.py`)

A ~200-line stdlib-only forward proxy used when tinyproxy is absent:

- source-IP ACL from `ALLOW_SUBNETS` (403 otherwise), enforced in-process —
  no root, no iptables;
- HTTP CONNECT tunnels to ports 80/443 (the path model APIs actually use);
- plain-HTTP absolute-form forwarding (80/443) for any non-HTTPS fallback;
- one thread per connection; refuses to start if `ALLOW_SUBNETS` is empty
  (rather than run as an open relay).

Replacements for a root-capable operator: install tinyproxy (`yum install
tinyproxy`) and rerun — the script picks it up automatically.
