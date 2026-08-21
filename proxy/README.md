# Cluster forward proxy for external model APIs

JUNO HTCondor execute nodes have **no external internet**, so any model
provider reached over the public internet (DeepSeek, Zhipu, Anthropic, OpenAI,
…) times out from worker nodes. The internal gateway `aiapi.ihep.ac.cn` works
around this only for the models IHEP serves.

To use **any** third-party provider from condor jobs, run an HTTP CONNECT
forward proxy on a jump host that does have direct outbound internet (e.g.
`192.168.237.165`), then point SimpleEvolution condor jobs at it.

## 1. Stand up the proxy on the jump host

```bash
# On 192.168.237.165 (has direct internet), from the repo root:
sudo ALLOW_SUBNETS="192.168.0.0/16 10.0.0.0/8" ./proxy/setup_proxy.sh
```

This installs **tinyproxy** (systemd daemon, port 3128) when it can, or falls
back to **pproxy** (user-space `pip install pproxy`, no root). CONNECT is only
allowed for ports 443/80, and only for the subnets in `ALLOW_SUBNETS` — keep
that list narrowed to the JUNO execute-node subnets so the host is not an open
relay.

Verify from a condor **execute node** (not the jump host):

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
