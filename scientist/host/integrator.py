"""Temporary main-writer role for one Supervisor integration request."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .agent_runtime import AgentRuntime
from .research_agent import AgentError, ResearchAgent, WorkingState
from ..research_files import ResearchFiles


class IntegratorError(AgentError):
    pass


@dataclass(frozen=True)
class IntegratorResult:
    outcome: str
    instruction: str | None = None
    working_model: str | None = None
    rationale: dict[str, Any] | None = None
    evidence_refs: tuple[str, ...] = ()
    donor_experiment_ids: tuple[str, ...] = ()
    reason: str | None = None


class _Session:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "session.jsonl"
        self.trajectory = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("role") in {"user", "assistant"} and isinstance(
                    item.get("content"), str,
                ):
                    self.trajectory.append({
                        "role": item["role"], "content": item["content"],
                    })

    def append_message(self, role, content, *, round_id):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "role": role, "content": content, "round": round_id,
            }, ensure_ascii=False) + "\n")


class IntegratorTools:
    """Read-only target-world navigation; no source writes or private L3."""

    def __init__(self, *, workspace: Path, repo: Path, scratch: Path):
        self.files = ResearchFiles(
            work=workspace, repo=repo, scratch=scratch, cap_chars=12_000,
        )

    def execute(self, action, **kwargs):
        try:
            if action["action"] == "read_file":
                return self.files.read_file(
                    action["path"], offset=action.get("offset", 1),
                    limit=action.get("limit", 400),
                )
            if action["action"] == "grep_files":
                return self.files.grep_files(
                    action["pattern"], path=action.get("path", "/work"),
                    glob=action.get("glob"), context=action.get("context", 0),
                    max_matches=action.get("max_matches", 50),
                )
            if action["action"] == "glob_files":
                return self.files.glob_files(
                    action["pattern"], path=action.get("path", "/work"),
                    limit=action.get("limit", 200),
                )
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "Integrator tools are read-only"}


class IntegratorAgent(ResearchAgent):
    """Fresh request-scoped identity; never inherits donor private cognition."""

    _error_class = IntegratorError
    _protocol_reminder = (
        "Return exactly one JSON object: submit_synthesis using only request "
        "donors, or abstain with a reason."
    )

    def __init__(self, *, model, timeout_seconds: int, max_steps: int = 4):
        super().__init__(
            model=model, runtime=None, timeout_seconds=timeout_seconds,
            max_steps=max_steps, command_timeout_seconds=0,
            command_output_cap_chars=0,
        )
        self._allowed_donors: set[str] = set()

    def _parse_action(self, text: str) -> list[dict]:
        try:
            action = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise IntegratorError("invalid JSON") from exc
        if not isinstance(action, dict):
            raise IntegratorError("action must be an object")
        name = action.get("action")
        if name in {"read_file", "grep_files", "glob_files"}:
            required = "path" if name == "read_file" else (
                "pattern" if name in {"grep_files", "glob_files"} else ""
            )
            if required not in action:
                raise IntegratorError(f"{name} requires {required}")
            return [action]
        if name == "abstain":
            if not str(action.get("reason", "")).strip():
                raise IntegratorError("abstain requires reason")
            return [action]
        if name != "submit_synthesis":
            raise IntegratorError("expected submit_synthesis or abstain")
        required = {
            "instruction", "working_model", "rationale",
            "evidence_refs", "donor_experiment_ids",
        }
        if required - set(action):
            raise IntegratorError("incomplete synthesis")
        donors = action["donor_experiment_ids"]
        if (
            not isinstance(donors, list) or not donors
            or not set(donors).issubset(self._allowed_donors)
        ):
            raise IntegratorError("synthesis donors must be a non-empty request subset")
        return [action]

    def integrate(
        self,
        request: dict[str, Any],
        *,
        public_evidence: dict[str, Any],
        session_dir: Path | None = None,
        workspace: Path | None = None,
        repo: Path | None = None,
    ) -> IntegratorResult:
        self._allowed_donors = set(request["donor_experiment_ids"])

        def run(directory: Path) -> IntegratorResult:
            session = _Session(directory)
            seed = json.dumps({
                "integration_request": request,
                "public_evidence": public_evidence,
            }, ensure_ascii=False)
            messages = list(session.trajectory)
            if messages:
                seed = "Resume the same request after interruption. Current public facts: " + seed
            messages.append({"role": "user", "content": seed})
            session.append_message("user", seed, round_id=0)

            def terminal(action, state, usages, step, outcome):
                if action is None:
                    return IntegratorResult(
                        outcome="abstained", reason="step budget exhausted",
                    )
                if action["action"] == "abstain":
                    return IntegratorResult(
                        outcome="abstained", reason=str(action["reason"]),
                    )
                return IntegratorResult(
                    outcome="submitted",
                    instruction=str(action["instruction"]),
                    working_model=str(action["working_model"]),
                    rationale=dict(action["rationale"]),
                    evidence_refs=tuple(str(x) for x in action["evidence_refs"]),
                    donor_experiment_ids=tuple(
                        str(x) for x in action["donor_experiment_ids"]
                    ),
                )

            source_root = Path(workspace) if workspace is not None else Path(".")
            return AgentRuntime(
                self,
                source_read_actions=("read_file", "grep_files", "glob_files"),
            ).run(
                system_prompt=(Path(__file__).parent.parent / "prompts" / "integrator.md").read_text(),
                messages=messages,
                session=session, current_round=0,
                steps_budget=self.max_steps, source_root=source_root,
                build_tools=lambda scratch, home: IntegratorTools(
                    workspace=source_root,
                    repo=Path(repo) if repo is not None else source_root,
                    scratch=Path(scratch),
                ),
                terminal_name=("submit_synthesis", "abstain"),
                budget_nudge="Submit one synthesis or abstain now.",
                handle_terminal=terminal,
                compact=lambda messages, usages, state: None,
                checkpoint=lambda *args, **kwargs: None,
                state=WorkingState(),
            )

        if session_dir is not None:
            return run(Path(session_dir))
        with TemporaryDirectory(prefix="simpleevo-integrator-") as temporary:
            return run(Path(temporary))
