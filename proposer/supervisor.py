"""Supervisor contracts: the research tree's persistent growth gate."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from .agent_runtime import AgentRuntime
from .research_agent import AgentError, ResearchAgent
from .scientist import (
    ContextPolicy,
    _bump,
    _compact_live_messages,
    _estimate_tokens,
    _prompt_tokens,
)
from .scientist_session import ScientistSession


class SupervisorError(AgentError):
    pass


SUPERVISOR_TOOL_CONTRACT = """\
## Read-only investigation tools

You investigate the public research environment yourself.  Your wake batch
already carries the first-hand facts — each terminal event's measured
metrics, the allocatable candidates with their metrics and open seat
counts, the seat ledger (what has been bought where, with what outcome),
the untried lens set per node, per-lens output statistics, and the budget's
used/remaining amounts — so reach for these tools only when you need deeper
history: lineage, a proposal's text, a research memo, or coverage search.
Facts only are
returned; nothing here ranks nodes, lenses, or purchases.

- `inspect_node` {node_id}: one Node and its direct children.
- `compare_nodes` {node_ids: [..]}: factual side-by-side metrics.
- `lineage` {node_id}: root-to-node ancestry.
- `search_experiments` {query, filters?, limit?}: coverage-oriented search
  over Experiment records.
- `inspect_experiment` {experiment_id}: one Proposal/Experiment outcome.
- `inspect_originating_research_state` {experiment_id}: the attributed
  research memo behind an Experiment; you must `inspect_experiment` first.
- `list_nodes` {}: every Node in the tree, including parked, dormant, and
  prior-epoch Nodes, with a mechanical `allocatable` flag and the lenses
  its own episodes have burned (lineage-dedup is computed against the full
  ancestry — see the untried fact in your batch).
- `inspect_node_allocations` {node_id}: seat investment (each allocation
  with its lens) and resulting public outcomes for one Node.
- `inspect_run_status` {}: mechanical run facts — in-flight work, queued
  proposals, open seats, configured capacity, and (when the driver set
  budget policy) the `budget` block: terminal evals vs the eval cap,
  spend vs the USD budget, remaining amounts, and whether the run is
  already capped.

These tools are read-only.  The public research ledger is authoritative; your
notebook is your own revisable account and never overrides it.
"""


class SupervisorTools:
    """Read-only dispatch over the public research environment (design §6)."""

    def __init__(self, memory, *, runtime_facts: dict | None = None):
        self.memory = memory
        self.runtime_facts = dict(runtime_facts or {})
        self.inspected_experiment_ids: set[str] = set()

    def execute(self, action: dict, **_) -> dict:
        try:
            return self._execute(action or {})
        except Exception as exc:  # never raise into the agent loop
            return {"ok": False, "error": str(exc)}

    def _execute(self, action: dict) -> dict:
        name = action.get("action")
        if name == "inspect_node":
            return self.memory.inspect_node(str(action["node_id"]))
        if name == "compare_nodes":
            return self.memory.compare_nodes(
                [str(item) for item in action["node_ids"]])
        if name == "lineage":
            return self.memory.lineage(str(action["node_id"]))
        if name == "search_experiments":
            return self.memory.search_experiments(
                str(action.get("query", "")),
                filters=action.get("filters") or None,
                limit=int(action.get("limit", 10)),
                buckets=bool(action.get("buckets", True)),
            )
        if name == "inspect_experiment":
            result = self.memory.inspect_experiment(
                str(action["experiment_id"]))
            if result.get("ok", True):
                self.inspected_experiment_ids.add(result["experiment_id"])
            return result
        if name == "inspect_originating_research_state":
            experiment_id = str(action["experiment_id"])
            if experiment_id not in self.inspected_experiment_ids:
                return {
                    "ok": False,
                    "error": "inspect the experiment first",
                }
            return self.memory.inspect_originating_research_state(
                experiment_id)
        if name == "list_nodes":
            return self._list_nodes()
        if name == "inspect_node_allocations":
            return self.memory.node_allocations(str(action["node_id"]))
        if name == "inspect_run_status":
            status = self.memory.run_status(
                pricing=self.runtime_facts.get("pricing"))
            status["config"] = dict(self.runtime_facts)
            running = status["running_attempts"].get("proposer", 0)
            status["free_proposer_capacity"] = max(
                0, int(self.runtime_facts.get("max_proposer_inflight", 0))
                - running)
            return status
        return {"ok": False, "error": f"unsupported supervisor action: {name}"}

    def _list_nodes(self) -> dict:
        # Own burned lenses from the same projection the batch facts use;
        # open seats counted from allocations directly (a lens-less
        # frontier lease is still a seat in flight).
        seats_open = self.memory.queries.open_allocation_counts_by_node()
        burned_own: dict[str, set[str]] = {}
        for row in self.memory.queries.episode_operator_rows():
            burned_own.setdefault(row["node_id"], set()).add(row["lens"])
        rows = []
        for node in self.memory.queries.list_nodes():
            rows.append({
                "node_id": node.node_id,
                "parent_node_id": node.parent_node_id,
                "depth": node.depth,
                "status": node.status,
                "metrics": dict(node.metrics),
                "gate_passed": node.gate_result.passed,
                # Seat semantics: a living node is purchasable for any lens
                # its lineage has not burned (the untried batch fact holds
                # the exact per-node set).  There are no per-node research
                # or proposal caps — the budget is the boundary.
                "allocatable": node.status != "dead",
                "seats_inflight": seats_open.get(node.node_id, 0),
                "lenses_burned_here": sorted(
                    burned_own.get(node.node_id, ())),
            })
        return {"ok": True, "nodes": rows}


SUPERVISOR_PROMPT_VERSION = "supervisor-v2-seats"


def load_supervisor_session(
    run_dir: Path,
    *,
    prompt_version: str = SUPERVISOR_PROMPT_VERSION,
) -> "ScientistSession":
    """One persistent growth-gate identity per run (design §5).

    Reuses the Scientist continuity machinery: append-only session.jsonl,
    a revisable notebook.md, and meta.json holding identity plus an audit
    mirror of the authoritative (scheduler-side) event cursor.
    """
    session_dir = Path(run_dir) / "supervisor" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    return ScientistSession._load_from_dir(session_dir, prompt_version)


@dataclass(frozen=True)
class SupervisorTurnResult:
    """One committed judgment; identity and cursor come from the harness.

    ``seat_purchases`` is the growth decision: (node_id, lens) pairs, each
    one seat.  Empty purchases = wait for in-flight evidence (the scheduler
    rejects an empty stop while untried seats remain).
    """

    decision_kind: str  # growth | integration_request | epoch_review
    seat_purchases: tuple[tuple[str, str], ...] = ()
    rationale: str = ""
    detail: dict[str, Any] | None = None


_COLD_START = (
    "You are the Supervisor of this research tree, resumed for the first "
    "time. The seed world (root Node) is ready, and you hold exclusive "
    "authority over the tree's growth: every research seat in this run "
    "exists only because you bought it — a node and a lens, worth one "
    "seat's cost.\n"
    "Investigate the public environment with your tools, then judge. "
    "Buying nothing is available and sometimes right while evidence is in "
    "flight — but a terminal is final for the turn: with no work in flight "
    "and untried seats still on the board, an empty purchase list is "
    "rejected by the harness, so investigate before you submit, never as "
    "a way to pause."
)

_SUPERVISOR_SUSPEND_PROMPT = (
    "Your turn is ending. Rewrite your notebook — first person, as your own "
    "revisable account of the research landscape: which lineages you funded "
    "or refused and why, which look promising or exhausted, and what you are "
    "waiting for. It is memory, not fact: the public research ledger always "
    "wins where they disagree.\n"
    'Return one JSON object: {"notebook": "<your notebook text>"}'
)


class SupervisorAgent(ResearchAgent):
    """Persistent growth gate: investigates, then grants or withholds leases.

    Woken only by evidence-change event batches; every resumption restores
    the same logical identity via the shared session. The semantic output is
    deliberately minimal — selected Node IDs and one rationale.
    """

    _error_class = SupervisorError
    _protocol_reminder = (
        "Return exactly one JSON object: submit_growth_decision "
        "{seat_purchases, rationale} (each purchase is {node_id, lens}; an "
        "empty list waits), submit_integration_request, or "
        "submit_epoch_review."
    )
    _TERMINALS = (
        "submit_growth_decision",
        "submit_integration_request",
        "submit_epoch_review",
    )
    _TOOL_ACTIONS = frozenset({
        "inspect_node", "compare_nodes", "lineage", "search_experiments",
        "inspect_experiment", "inspect_originating_research_state",
        "list_nodes", "inspect_node_allocations", "inspect_run_status",
    })

    def __init__(
        self,
        *,
        model,
        timeout_seconds: int,
        max_steps: int = 40,
        context_policy: "ContextPolicy | None" = None,
        usage_observer=None,
    ):
        super().__init__(
            model=model,
            runtime=None,
            timeout_seconds=timeout_seconds,
            max_steps=max_steps,
            command_timeout_seconds=0,
            command_output_cap_chars=0,
            usage_observer=usage_observer,
        )
        self._context_policy = context_policy or ContextPolicy()

    def _parse_action(self, text: str) -> list[dict]:
        try:
            action = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SupervisorError("invalid JSON") from exc
        if not isinstance(action, dict):
            raise SupervisorError("action must be an object")
        name = action.get("action")
        if name in self._TOOL_ACTIONS:
            return [action]
        if name == "submit_growth_decision":
            extra = set(action) - {
                "action", "seat_purchases", "rationale"}
            if extra:
                raise SupervisorError(
                    "growth decision may contain only seat_purchases and "
                    f"rationale; unexpected: {sorted(extra)}"
                )
            purchases = action.get("seat_purchases")
            if not isinstance(purchases, list):
                raise SupervisorError(
                    "seat_purchases must be a list of {node_id, lens}")
            for item in purchases:
                if not isinstance(item, dict) or set(item) != {
                        "node_id", "lens"}:
                    raise SupervisorError(
                        "each seat purchase must be exactly "
                        "{node_id, lens}"
                    )
                if not isinstance(item["node_id"], str) or not item["node_id"]:
                    raise SupervisorError("purchase node_id must be non-empty")
                if not isinstance(item["lens"], str) or not item["lens"]:
                    raise SupervisorError("purchase lens must be non-empty")
            if not str(action.get("rationale", "")).strip():
                raise SupervisorError("rationale is required")
            return [action]
        if name == "submit_integration_request":
            required = {
                "target_node_id", "donor_experiment_ids",
                "selection_rationale",
            }
            missing = required - set(action)
            if missing:
                raise SupervisorError(
                    f"missing integration fields: {sorted(missing)}")
            # The request id is mechanical: the harness assigns it from the
            # work (stable across retries of the same batch), so a model
            # supplying one is a contract violation, same as growth's
            # extra-field rejection.
            extra = set(action) - {"action"} - required
            if extra:
                raise SupervisorError(
                    f"unsupported integration fields: {sorted(extra)}")
            return [action]
        if name == "submit_epoch_review":
            if action.get("review") not in {"promote", "retain"}:
                raise SupervisorError("epoch review must be promote or retain")
            missing = {
                "integration_request_id", "review", "rationale",
            } - set(action)
            if missing:
                raise SupervisorError(
                    f"missing epoch review fields: {sorted(missing)}")
            return [action]
        raise SupervisorError(
            "expected a tool action or one of the three supervisor terminals")

    def resume(
        self,
        *,
        session: "ScientistSession",
        tools: SupervisorTools,
        batch: dict[str, Any],
        run_context: dict[str, Any] | None = None,
    ) -> SupervisorTurnResult:
        """Run one wake-up turn over an incremental event batch."""
        turn = int(session.meta.get("supervisor_turn") or 0) + 1
        content = json.dumps(batch, ensure_ascii=False)
        if session.is_first_round():
            goal = str((run_context or {}).get("goal") or "").strip()
            content = (
                _COLD_START
                + (f"\nResearch goal: {goal}" if goal else "")
                + "\n\n" + content
            )
        messages = [{"role": "user", "content": content}]
        session.append_message("user", content, round_id=turn)
        system_prompt = self._build_system_prompt(session)

        def terminal(action, state, usages, step, outcome):
            if action is None:
                raise SupervisorError("Supervisor exhausted its step budget")
            name = action["action"]
            session.meta["supervisor_turn"] = turn
            session.save_meta(round_id=turn)
            if name == "submit_growth_decision":
                return SupervisorTurnResult(
                    decision_kind="growth",
                    seat_purchases=tuple(
                        (str(item["node_id"]), str(item["lens"]))
                        for item in action["seat_purchases"]
                    ),
                    rationale=str(action["rationale"]),
                )
            if name == "submit_integration_request":
                rationale = str(action["selection_rationale"])
                return SupervisorTurnResult(
                    decision_kind="integration_request",
                    rationale=rationale,
                    detail={
                        "target_node_id": str(action["target_node_id"]),
                        "donor_experiment_ids": [
                            str(item)
                            for item in action["donor_experiment_ids"]
                        ],
                        "selection_rationale": rationale,
                    },
                )
            return SupervisorTurnResult(
                decision_kind="epoch_review",
                rationale=str(action["rationale"]),
                detail={
                    "integration_request_id": str(
                        action["integration_request_id"]),
                    "review": str(action["review"]),
                    "rationale": str(action["rationale"]),
                },
            )

        return AgentRuntime(self).run(
            system_prompt=system_prompt,
            messages=messages,
            session=session,
            current_round=turn,
            steps_budget=self.max_steps,
            source_root=Path("."),
            build_tools=lambda scratch, home: tools,
            terminal_name=self._TERMINALS,
            budget_nudge=(
                "Return your judgment now: submit_growth_decision "
                "(seat_purchases), submit_integration_request, or "
                "submit_epoch_review."
            ),
            handle_terminal=terminal,
            compact=self._compact,
            checkpoint=self._checkpoint,
        )

    def _build_system_prompt(self, session: "ScientistSession") -> str:
        parts = [_supervisor_prompt(), SUPERVISOR_TOOL_CONTRACT]
        if session.notebook.strip():
            parts.append(
                "## Your notebook (revisable autobiographical memory)\n\n"
                "This is your own prior account, not an authoritative fact "
                "source; the public research ledger wins whenever they "
                "disagree.\n\n" + session.notebook.strip()
            )
        return "\n\n".join(parts)

    def _compact(self, messages: list[dict], usages: list, state) -> None:
        """Same live-context policy as the Scientist; archive untouched."""
        policy = self._context_policy
        threshold = policy.emergency_threshold_tokens
        if threshold is None:
            return
        usage = usages[-1] if usages else None
        tokens = _prompt_tokens(usage)
        if tokens is None:
            tokens = _estimate_tokens(messages)
        if tokens <= threshold:
            return
        new_messages, info = _compact_live_messages(
            messages,
            window_pairs=policy.window_pairs,
            window_max_chars=policy.window_max_chars,
        )
        if not info["compacted"]:
            return
        messages[:] = new_messages
        _bump(state, "compact")

    def _checkpoint(
        self, system_prompt, messages, state, session, deadline, usages,
        current_round, *, capture_expectations: bool = False,
    ) -> None:
        """Rewrite the notebook before suspension (best-effort)."""
        remaining = deadline - time.monotonic()
        if remaining <= 5:
            return
        prompt_messages = list(messages) + [{
            "role": "user", "content": _SUPERVISOR_SUSPEND_PROMPT,
        }]
        try:
            reply = self.model.complete(
                system=system_prompt, messages=prompt_messages,
                timeout_seconds=remaining,
            )
        except Exception as exc:
            print(f"[supervisor] notebook checkpoint failed: {exc}",
                  flush=True)
            return
        if reply.usage is not None:
            usages.append(reply.usage)
            if self.usage_observer is not None:
                self.usage_observer(reply.usage)
        try:
            note = json.loads(reply.text).get("notebook")
        except (json.JSONDecodeError, TypeError, AttributeError):
            note = None
        if isinstance(note, str) and note.strip():
            session.write_notebook(note.strip())
            session.append_message(
                "user", _SUPERVISOR_SUSPEND_PROMPT, round_id=current_round)
            session.append_message(
                "assistant", reply.text, round_id=current_round)


def _supervisor_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "supervisor.md").read_text(
        encoding="utf-8",
    )
