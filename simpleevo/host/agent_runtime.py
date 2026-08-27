"""Shared model/tool/session loop for cognitive research roles."""
from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .research_agent import (
    WorkingState,
    _bump,
    _fingerprint,
    _register_evidence,
    _stamp,
)


class AgentRuntime:
    """Run one role episode while the role supplies semantics and tools.

    The runtime owns iteration, budgets, tool dispatch, session archival and
    terminal detection. A role supplies its tool registry, compaction/session
    policy, checkpoint behavior, and typed terminal-result constructor.
    """

    def __init__(self, agent, *, source_read_actions=()):
        self.agent = agent
        self.source_read_actions = frozenset(source_read_actions)

    def run(
        self,
        *,
        system_prompt: str,
        messages: list[dict],
        session,
        current_round: int,
        steps_budget: int,
        source_root: Path,
        build_tools,
        terminal_name: str | tuple[str, ...],
        budget_nudge: str,
        handle_terminal,
        time_nudge: str | None = None,
        compact,
        checkpoint,
        capture_expectations: bool = False,
        state: WorkingState | None = None,
    ):
        state = state or WorkingState()
        terminal_names = (
            {terminal_name} if isinstance(terminal_name, str)
            else set(terminal_name)
        )
        deadline = time.monotonic() + self.agent.timeout_seconds
        usages: list = []
        reminder_step = int(0.8 * steps_budget)
        reminded = False
        time_reminded = False
        started = time.monotonic()

        with TemporaryDirectory(prefix="simpleloop-scratch-") as scratch, \
                TemporaryDirectory(prefix="simpleloop-session-") as session_root:
            home = Path(session_root) / "home"
            home.mkdir(mode=0o700)
            tools = build_tools(scratch, home)
            compact(messages, [], state)

            for index in range(steps_budget):
                step = index + 1
                print(
                    f"[{_stamp()}] [agent runtime step {step}/{steps_budget}] "
                    "thinking",
                    flush=True,
                )
                if not reminded and reminder_step > 0 and step >= reminder_step:
                    messages.append({"role": "user", "content": budget_nudge})
                    reminded = True
                # Wall-clock pacing: the step nudge fires on 80% of STEPS,
                # but a hand-working seat can burn the whole wall with
                # two-thirds of its steps unspent (probe A round 2 died at
                # the deadline mid-exploration).  Fires once, at 80% of the
                # wall budget — information, not a stop order.
                if (time_nudge and not time_reminded
                        and time.monotonic() - started
                        >= 0.8 * self.agent.timeout_seconds):
                    messages.append({"role": "user", "content": time_nudge})
                    time_reminded = True
                # Graceful wall exit: with less than ~10% of the wall (cap
                # 90s) left there is no room for another model call plus a
                # conclusion — end the loop NOW so the lease concludes
                # cut_off on file (design §2.3: 预算断 = 出口三,不是 infra
                # 暴毙重发整个 attempt).  The margin scales with the budget
                # so short-budget tests keep their full loop.  The hard
                # deadline still guards hung tool calls below.
                wall_margin = min(90.0, 0.1 * self.agent.timeout_seconds)
                if deadline - time.monotonic() < wall_margin:
                    print(
                        f"[{_stamp()}] [agent runtime step {step}/"
                        f"{steps_budget}] wall nearly spent; concluding "
                        "cut_off",
                        flush=True,
                    )
                    break

                actions, reply_text = self.agent._step(
                    state,
                    messages,
                    system_prompt,
                    deadline,
                    usages,
                    step,
                    source_root=source_root,
                    steps_budget=steps_budget,
                )

                if len(actions) == 1 and actions[0]["action"] in terminal_names:
                    action = actions[0]
                    state.action_log.append({"action": action["action"], "step": step})
                    _bump(state, action["action"])
                    messages.append({"role": "assistant", "content": reply_text})
                    session.append_message(
                        "assistant", reply_text, round_id=current_round,
                    )
                    checkpoint(
                        system_prompt,
                        messages,
                        state,
                        session,
                        deadline,
                        usages,
                        current_round,
                        capture_expectations=capture_expectations,
                    )
                    return handle_terminal(
                        action, state, usages, step, "submit",
                    )

                results = []
                for action in actions:
                    name = action["action"]
                    state.action_log.append({"action": name, "step": step})
                    observation = tools.execute(
                        action, deadline=deadline, working_state=state,
                    )
                    _bump(state, "tool")
                    _register_evidence(state, action, observation)
                    state.last_tool_fingerprint = _fingerprint(action)
                    if observation.get("ok") and name in self.source_read_actions:
                        _bump(state, "source_read")
                        state.located = True
                    results.append(observation)

                envelope = json.dumps(
                    {"tool_results": results}, ensure_ascii=False,
                )
                messages.extend([
                    {"role": "assistant", "content": reply_text},
                    {"role": "user", "content": envelope},
                ])
                session.append_message(
                    "assistant", reply_text, round_id=current_round,
                )
                session.append_message("user", envelope, round_id=current_round)
                compact(messages, usages, state)

            checkpoint(
                system_prompt,
                messages,
                state,
                session,
                deadline,
                usages,
                current_round,
                capture_expectations=capture_expectations,
            )
            return handle_terminal(
                None, state, usages, steps_budget, "abstain",
            )
