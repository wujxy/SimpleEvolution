# SimpleEvolution

Event-driven Research Tree evolution: **Node → Proposer → Proposal →
Experiment → New Node**. A Scientist thread proposes changes to a research
target; an Executor implements them; the Harness measures, gates, and advances
a GEPA-style per-axis frontier.

Design source of truth: [docs/simpleevolution_design.md](docs/simpleevolution_design.md).
Implementation notes: [docs/simpleevolution_implementation.md](docs/simpleevolution_implementation.md).

## Install

```bash
pip install -e .            # provides the `simpleevo` command
# or run without installing:
python -m simpleevo --help
```

## Commands

```bash
# 1. Prepare the environment: git repo + image check + run-dir + root node.
simpleevo --run-dir runs/tiny-001 init --config examples/tiny_algo_opt/task.yaml

# 2. Start the evolution (init is implied if not already done).
simpleevo --run-dir runs/tiny-001 run --config examples/tiny_algo_opt/task.yaml

# 3. Continue an existing run (loads runs/<id>/task.yaml; reconciles offline results).
simpleevo --run-dir runs/tiny-001 resume

# Inspect state.
simpleevo --run-dir runs/tiny-001 status
simpleevo --run-dir runs/tiny-001 inspect --node <node_id>
simpleevo --run-dir runs/tiny-001 reseed --node <node_id>
```

Worked examples:

- [examples/tiny_algo_opt/](examples/tiny_algo_opt/) — pure-Python pair-counting
  speed task (`tinyalgo`).
- [examples/xsbench_opt/](examples/xsbench_opt/) — compiled-C benchmark: optimize
  the XSBench macroscopic cross-section lookup kernel for single-threaded
  lookups/s while keeping its verification checksum bit-identical. Ships a
  hidden human-expert reference for comparison (`reference/`).

## Job backends (local vs. HTCondor)

Workers (proposer + experiment) are submitted through one of two backends that
share the same `BaseSubmitter` interface and the same manifest/result path
layout — swapping backends is a config change, not a code change:

```yaml
jobs:
  backend: local        # subprocess (default) — or "condor"
```

HTCondor backend (`jobs.backend: condor`) submits each worker as a vanilla
condor job. Add the cluster knobs under `jobs` (the IHEP/JUNO example):

```yaml
jobs:
  backend: condor
  collector: cm01.ihep.ac.cn          # JUNO production pool (not the login default)
  schedd_name: scheduler@schedd12.ihep.ac.cn
  accounting_group: JUNO.juno.default
  accounting_group_user: lidian       # defaults to your login user
  request_os: AlmaLinux9
  cpu_model: zen5                     # optional: zen4 | zen5 | icelake | skylake
  machine_constraint: 'Machine == "lhws316.ihep.ac.cn"'   # optional
  memory_mb: 4096
  cpus: 1
  python_executable: /lustrefs/.../python   # shared-FS interpreter for execute nodes
```

`collector`/`schedd_name` select the JUNO production schedd; they are required
on login nodes whose default collector sees only the small inkcm pool. Condor
jobs source a run-scoped `run_dir/job_env.sh` that exports the simpleevo
packages' `PYTHONPATH` and the forwarded API-key/proxy env vars, so a worker on
a different execute node gets the same environment a local subprocess would.
Each submission is recorded in `run_dir/jobs.json`, and the scheduler
reconciles in-flight jobs against the live `condor_q` on resume (HELD/gone
jobs are marked infra-failed and retried).

### Reaching external model APIs from condor jobs

JUNO execute nodes have **no external internet**, so third-party providers
(DeepSeek, Zhipu, Anthropic, OpenAI, …) time out from worker nodes. To route
their API traffic through a jump host that does have internet, set a forward
proxy under `jobs` — it is written into `job_env.sh` and honoured by both the
proposer's SDKs and the executor's `claude` CLI (and forwarded into the
Apptainer containers):

```yaml
jobs:
  backend: condor
  # ... existing collector/schedd/accounting settings ...
  http_proxy:  http://192.168.237.165:3128
  https_proxy: http://192.168.237.165:3128
  no_proxy: localhost,127.0.0.1,aiapi.ihep.ac.cn   # keep internal endpoints off the proxy
```

`http_proxy`/`https_proxy` are emitted as both upper- and lower-case env vars.
When any proxy is set and `no_proxy` is omitted it defaults to
`localhost,127.0.0.1`. Unset fields forward the submit host's own proxy env as
before. Setup for the jump host itself: [proxy/](proxy/).

