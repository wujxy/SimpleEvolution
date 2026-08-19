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
    context: Mapping[str, Any] = field(default_factory=dict)
    prompt_dir: Path | None = None
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

    @property
    def workspace_path(self) -> Path:
        """Default workspace for proposer investigation is the repo itself.

        Workers may create a git worktree for a specific Node SHA if needed.
        """
        return self.repo_path

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
            "context": dict(self.context),
            "prompt_dir": str(self.prompt_dir) if self.prompt_dir else None,
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
            context=dict(raw.get("context", {})),
            prompt_dir=Path(prompt_dir) if prompt_dir else None,
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
