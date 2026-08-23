"""Task configuration for SimpleEvolution.

A task config describes the research goal, the repository, the runtime,
which paths may be edited, how to evaluate a change, and the measured axes
that define the frontier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class JobConfig:
    """Which job backend the Scheduler submits workers through, and its knobs.

    ``backend`` selects the submitter: ``local`` (subprocess) or ``condor``.
    The remaining fields are condor-only and ignored when ``backend ==
    ``local``.  ``collector``/``schedd_name`` select the JUNO production pool
    (cm01.ihep.ac.cn) — required on login nodes whose default collector sees
    the wrong 4-machine pool.
    """

    backend: str = "local"
    collector: str | None = None
    schedd_name: str | None = None
    accounting_group: str = "JUNO.juno.default"
    accounting_group_user: str = ""
    ihep_group: str | None = None
    request_os: str = "AlmaLinux9"
    cpu_model: str | None = None
    machine_constraint: str | None = None
    memory_mb: int = 4096
    cpus: int = 1
    python_executable: str = ""
    # Forward proxy for the worker's outbound model/API traffic. JUNO execute
    # nodes have no external internet, so external providers must be reached
    # through a jump host's HTTP CONNECT proxy (e.g. 192.168.237.165:3128).
    # These are authoritative for condor jobs — independent of the submit
    # host's own proxy env. ``no_proxy`` keeps internal endpoints (e.g.
    # aiapi.ihep.ac.cn) off the proxy; when any proxy is set it defaults to
    # localhost-only.
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = ""
    submit_cmd: str = "condor_submit"
    query_cmd: str = "condor_q"
    remove_cmd: str = "condor_rm"
    poll_seconds: float = 15.0
    run_timeout_seconds: int = 7200
    idle_warn_seconds: int = 7200
    disappearance_grace_seconds: int = 120

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "collector": self.collector,
            "schedd_name": self.schedd_name,
            "accounting_group": self.accounting_group,
            "accounting_group_user": self.accounting_group_user,
            "ihep_group": self.ihep_group,
            "request_os": self.request_os,
            "cpu_model": self.cpu_model,
            "machine_constraint": self.machine_constraint,
            "memory_mb": self.memory_mb,
            "cpus": self.cpus,
            "python_executable": self.python_executable,
            "http_proxy": self.http_proxy,
            "https_proxy": self.https_proxy,
            "no_proxy": self.no_proxy,
            "submit_cmd": self.submit_cmd,
            "query_cmd": self.query_cmd,
            "remove_cmd": self.remove_cmd,
            "poll_seconds": self.poll_seconds,
            "run_timeout_seconds": self.run_timeout_seconds,
            "idle_warn_seconds": self.idle_warn_seconds,
            "disappearance_grace_seconds": self.disappearance_grace_seconds,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> JobConfig:
        return cls(
            backend=str(raw.get("backend", "local")),
            collector=raw.get("collector"),
            schedd_name=raw.get("schedd_name"),
            accounting_group=str(raw.get("accounting_group", "JUNO.juno.default")),
            accounting_group_user=str(raw.get("accounting_group_user", "")),
            ihep_group=raw.get("ihep_group"),
            request_os=str(raw.get("request_os", "AlmaLinux9")),
            cpu_model=raw.get("cpu_model"),
            machine_constraint=raw.get("machine_constraint"),
            memory_mb=int(raw.get("memory_mb", 4096)),
            cpus=int(raw.get("cpus", 1)),
            python_executable=str(raw.get("python_executable", "")),
            http_proxy=str(raw.get("http_proxy", "")),
            https_proxy=str(raw.get("https_proxy", "")),
            no_proxy=str(raw.get("no_proxy", "")),
            submit_cmd=str(raw.get("submit_cmd", "condor_submit")),
            query_cmd=str(raw.get("query_cmd", "condor_q")),
            remove_cmd=str(raw.get("remove_cmd", "condor_rm")),
            poll_seconds=float(raw.get("poll_seconds", 15.0)),
            run_timeout_seconds=int(raw.get("run_timeout_seconds", 7200)),
            idle_warn_seconds=int(raw.get("idle_warn_seconds", 7200)),
            disappearance_grace_seconds=int(raw.get("disappearance_grace_seconds", 120)),
        )

    def proxy_env(self) -> dict[str, str]:
        """Env-var overlay routing worker traffic through the configured proxy.

        Emits both upper- and lower-case proxy vars, because different HTTP
        clients read different key names (httpx/requests read the upper-case
        forms; some CLIs read the lower-case ones). ``no_proxy`` defaults to
        localhost-only when any proxy is configured, so loopback never goes
        through the proxy; set it explicitly to keep internal endpoints (e.g.
        aiapi.ihep.ac.cn) off the proxy. Returns ``{}`` when nothing is
        configured — the caller then forwards the submit host's env unchanged.
        """
        overlay: dict[str, str] = {}
        for key, value in (("HTTP_PROXY", self.http_proxy),
                           ("HTTPS_PROXY", self.https_proxy)):
            if value:
                overlay[key] = value
                overlay[key.lower()] = value
        no_proxy = self.no_proxy or ("localhost,127.0.0.1" if overlay else "")
        if no_proxy:
            overlay["NO_PROXY"] = no_proxy
            overlay["no_proxy"] = no_proxy
        return overlay


@dataclass(frozen=True)
class EvolutionConfig:
    """Full task configuration shared by the CLI, Scheduler, and workers."""

    goal: str
    repo_path: Path
    runtime_image: Path
    editable_paths: tuple[str, ...]
    frozen_paths: tuple[str, ...]
    eval_commands: tuple[str, ...]
    metrics_schema: Mapping[str, Any]
    axes: tuple[str, ...]
    gate_block: str = ""
    root_sha: str | None = None
    runtime_binds: tuple[str, ...] = ()
    read_only_binds: tuple[str, ...] = ()
    researcher: Mapping[str, Any] = field(default_factory=dict)
    executor: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    pricing: Mapping[str, Any] = field(default_factory=dict)
    prompt_dir: Path | None = None
    jobs: JobConfig = field(default_factory=JobConfig)
    proposal_slots: int = 3
    scientist_steps: int = 200
    agent_timeout_seconds: int = 3600
    eval_timeout_seconds: int = 600
    command_timeout_seconds: int = 120
    command_output_cap_chars: int = 12000
    max_proposer_inflight: int = 2
    max_experiment_inflight: int = 2
    poll_seconds: float = 5.0
    queue_max_size: int = 10
    quiescence_window_proposals: int = 2
    root_fresh_scientists: int = 1
    frontier_policy: str = "gepa"
    frontier_top_k: int = 3
    supervisor_steps: int = 40
    supervisor_max_retries: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (safe for YAML/JSON)."""
        return {
            "goal": self.goal,
            "repo_path": str(self.repo_path),
            "runtime_image": str(self.runtime_image),
            "editable_paths": list(self.editable_paths),
            "frozen_paths": list(self.frozen_paths),
            "eval_commands": list(self.eval_commands),
            "metrics_schema": dict(self.metrics_schema),
            "axes": list(self.axes),
            "gate_block": self.gate_block,
            "root_sha": self.root_sha,
            "runtime_binds": list(self.runtime_binds),
            "read_only_binds": list(self.read_only_binds),
            "researcher": dict(self.researcher),
            "executor": dict(self.executor),
            "context": dict(self.context),
            "pricing": dict(self.pricing),
            "prompt_dir": str(self.prompt_dir) if self.prompt_dir else None,
            "jobs": self.jobs.to_dict(),
            "proposal_slots": self.proposal_slots,
            "scientist_steps": self.scientist_steps,
            "agent_timeout_seconds": self.agent_timeout_seconds,
            "eval_timeout_seconds": self.eval_timeout_seconds,
            "command_timeout_seconds": self.command_timeout_seconds,
            "command_output_cap_chars": self.command_output_cap_chars,
            "max_proposer_inflight": self.max_proposer_inflight,
            "max_experiment_inflight": self.max_experiment_inflight,
            "poll_seconds": self.poll_seconds,
            "queue_max_size": self.queue_max_size,
            "quiescence_window_proposals": self.quiescence_window_proposals,
            "root_fresh_scientists": self.root_fresh_scientists,
            "frontier_policy": self.frontier_policy,
            "frontier_top_k": self.frontier_top_k,
            "supervisor_steps": self.supervisor_steps,
            "supervisor_max_retries": self.supervisor_max_retries,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EvolutionConfig:
        """Load from a plain dict."""
        repo = Path(raw["repo_path"])
        image = Path(raw["runtime_image"])
        prompt_dir = raw.get("prompt_dir")
        return cls(
            goal=str(raw["goal"]),
            repo_path=repo,
            runtime_image=image,
            editable_paths=tuple(raw.get("editable_paths", [])),
            frozen_paths=tuple(raw.get("frozen_paths", [])),
            eval_commands=tuple(raw.get("eval_commands", [])),
            metrics_schema=dict(raw.get("metrics_schema", {})),
            axes=tuple(raw.get("axes", [])),
            gate_block=str(raw.get("gate_block", "")),
            root_sha=raw.get("root_sha"),
            runtime_binds=tuple(raw.get("runtime_binds", [])),
            read_only_binds=tuple(raw.get("read_only_binds", [])),
            researcher=dict(raw.get("researcher", {})),
            executor=dict(raw.get("executor", {})),
            context=dict(raw.get("context", {})),
            pricing=dict(raw.get("pricing", {})),
            prompt_dir=Path(prompt_dir) if prompt_dir else None,
            jobs=JobConfig.from_dict(raw.get("jobs", {})),
            proposal_slots=int(raw.get("proposal_slots", 3)),
            scientist_steps=int(raw.get("scientist_steps", 200)),
            agent_timeout_seconds=int(raw.get("agent_timeout_seconds", 3600)),
            eval_timeout_seconds=int(raw.get("eval_timeout_seconds", 600)),
            command_timeout_seconds=int(raw.get("command_timeout_seconds", 120)),
            command_output_cap_chars=int(raw.get("command_output_cap_chars", 12000)),
            max_proposer_inflight=int(raw.get("max_proposer_inflight", 2)),
            max_experiment_inflight=int(raw.get("max_experiment_inflight", 2)),
            poll_seconds=float(raw.get("poll_seconds", 5.0)),
            queue_max_size=int(raw.get("queue_max_size", 10)),
            quiescence_window_proposals=int(raw.get("quiescence_window_proposals", 2)),
            root_fresh_scientists=int(raw.get("root_fresh_scientists", 1)),
            frontier_policy=str(raw.get("frontier_policy", "gepa")),
            frontier_top_k=int(raw.get("frontier_top_k", 3)),
            supervisor_steps=int(raw.get("supervisor_steps", 40)),
            supervisor_max_retries=int(raw.get("supervisor_max_retries", 3)),
        )


def load_config(path: str | Path) -> EvolutionConfig:
    """Load an EvolutionConfig from a YAML file.

    Relative ``repo_path`` / ``runtime_image`` / ``prompt_dir`` values are
    resolved against the config file's own directory, so an example task can
    ship portable relative paths and still run from any working directory.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"config file {path} must contain a YAML mapping")
    base = path.resolve().parent
    for key in ("repo_path", "runtime_image", "prompt_dir"):
        value = raw.get(key)
        if value is None:
            continue
        candidate = Path(str(value))
        if not candidate.is_absolute():
            raw[key] = str(base / candidate)
    return EvolutionConfig.from_dict(raw)


def save_config(path: str | Path, config: EvolutionConfig) -> None:
    """Save an EvolutionConfig to a YAML file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False), encoding="utf-8")
