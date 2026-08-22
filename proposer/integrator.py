"""Temporary main-writer role for one Supervisor integration request."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .agent_runtime import AgentRuntime
from .research_agent import AgentError, ResearchAgent, WorkingState


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

    def append_message(self, role, content, *, round_id):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "role": role, "content": content, "round": round_id,
            }, ensure_ascii=False) + "\n")


class _NoTools:
    def execute(self, action, **kwargs):  # pragma: no cover
        raise IntegratorError(f"unknown Integrator action {action['action']!r}")


class IntegratorAgent(ResearchAgent):
    """Fresh request-scoped identity; never inherits donor private cognition."""

    _error_class = IntegratorError

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
    ) -> IntegratorResult:
        self._allowed_donors = set(request["donor_experiment_ids"])

        def run(directory: Path) -> IntegratorResult:
            messages = [{"role": "user", "content": json.dumps({
                "integration_request": request,
                "public_evidence": public_evidence,
            }, ensure_ascii=False)}]

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

            return AgentRuntime(self).run(
                system_prompt=(Path(__file__).parent / "prompts" / "integrator.md").read_text(),
                messages=messages,
                session=_Session(directory), current_round=0,
                steps_budget=self.max_steps, source_root=Path("."),
                build_tools=lambda scratch, home: _NoTools(),
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
