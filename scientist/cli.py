"""The scientist CLI — one agent, one world.

    python -m scientist.cli --spec spec.json --world DIR

Standalone, the way claude code is standalone: give it a directory (the
world) and a spec (goal, gates, lens, model, budget); the scientist
reads, deliberates, calls its claude assistant, runs experiments, and
walks itself to one of the two exits. Everything it produces — session,
research state, assistant transcripts, usage, and the final
``conclusion.json`` — lands inside the world under ``.scientist/``.

simpleevo uses the same one line: open a world container, write the
spec, run this command, read the world back at close. It never sits in
the loop; the spec is the whole opening handshake and
``conclusion.json`` the whole exit contract.

Spec shape (see docs/design; all keys optional unless marked):
    goal, gate_block, editable_paths[], base_sha, node_id,
    hints[], charter (overrides the packaged Scientist charter,
                      prompts/scientist.md),
    model{api?|model, base_url, api_key, reasoning_effort},
    assistant{command, model, effort, node_world, env{}},
    budget{steps, wall_seconds, command_timeout_seconds,
           command_output_cap_chars, consult_timeout_seconds,
           work_default_minutes, distill_word_cap,
           compact_keep_messages, compact_max_chars},
    opening_messages[{role, content}] (default: the cold start),
    paths{work, repo, scratch} — container namespace, when running in
    the world container (standalone defaults: --world everywhere).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .agent import (
    _COLD_START, _compact_native, _upsert_judgment_message, \
    build_system_prompt, run_episode,
)
from .assistant_tools import AssistantConfig, InWorldAssistant
from .ledger import LocalLedger
from .model import ModelError
from .model_stdlib import build_stdlib_chat_model
from .scientist_session import ScientistSession
from .world import LocalWorld

PROMPT_VERSION = "oneworld-v5"


def _resolve_roots(args, spec: dict) -> dict:
    """Map the namespace onto this machine.

    In-container the spec's ``paths`` are the container's own mounts
    (identity); standalone, ``--world`` is the /work root and repo
    defaults to it (a plain clone serves as its own /repo)."""
    paths = dict(spec.get("paths") or {})
    # resolve(): the boundaries block must carry absolute paths — a
    # relative --world makes the model guess disk locations (observed:
    # both xsbench arms opened with cd /root/runs/... and find /).
    work = Path(args.world).resolve()
    return {
        "work": work,
        "repo": Path(args.repo).resolve() if args.repo else Path(
            paths.get("repo") or work).resolve(),
        "scratch": (
            Path(args.scratch).resolve() if args.scratch else Path(
                paths.get("scratch")
                or work / ".scientist" / "scratch").resolve()
        ),
    }


def _opening_messages(spec: dict) -> list[dict]:
    messages = [
        {"role": str(m.get("role") or "user"),
         "content": str(m.get("content") or "")}
        for m in spec.get("opening_messages") or []
        if isinstance(m, dict) and str(m.get("content") or "").strip()
    ]
    return messages or [{"role": "user", "content": _COLD_START}]


def _write_conclusion(ledger_root: Path, result: dict) -> Path:
    """The exit contract file: what the harness reads when it recovers
    the world. Mechanical record — the conclusion as validated at the
    door, plus the step count and the action log."""
    path = ledger_root / "conclusion.json"
    path.write_text(
        json.dumps(
            {
                "conclusion": result["conclusion"],
                "outcome": result["outcome"],
                "steps": result["steps"],
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "actions": result["actions"],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _run_probe(spec: dict, args) -> int:
    """One model call against the assembled context: the cheap check
    that a spec produces the standing context and the first actions we
    expect (grounding first, coverage query first)."""
    roots = _resolve_roots(args, spec)
    budget = dict(spec.get("budget") or {})
    world = LocalWorld(
        work=roots["work"], repo=roots["repo"], scratch=roots["scratch"],
        timeout_seconds=int(budget.get("command_timeout_seconds", 360)),
        cap_chars=int(budget.get("command_output_cap_chars", 12000)),
    )
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world, config=AssistantConfig.from_spec(spec),
        ledger=ledger, episode_id=spec.get("episode_id") or "probe",
        has_benchmark=bool(spec.get("editable_paths")),
    )
    model = build_stdlib_chat_model(dict(spec.get("model") or {}))
    from .native_tools import NATIVE_TOOLS, native_actions

    system_prompt = build_system_prompt(spec, roots=roots)
    messages = _opening_messages(spec)
    reply = model.complete(
        system=system_prompt, messages=messages,
        timeout_seconds=float(budget.get("wall_seconds", 3600)),
        tools=list(NATIVE_TOOLS),
    )

    actions = native_actions(reply)
    print("=" * 70)
    print(f"PROBE: {len(actions)} actions, "
          f"{len(reply.tool_calls)} tool calls, "
          f"text {len(reply.text)} chars")
    for action in actions:
        shown = dict(action)
        if "_arguments_raw" in shown:
            shown.pop("_arguments_raw", None)
        print(json.dumps(shown, ensure_ascii=False, indent=2))
    if not actions:
        print(f"TEXT: {reply.text[:600]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scientist",
        description="One scientist, one world: research to an exit.",
    )
    parser.add_argument("--spec", required=True, type=Path,
                        help="spec.json (the whole opening handshake)")
    parser.add_argument("--world", required=True, type=Path,
                        help="the world directory (the /work root here)")
    parser.add_argument("--repo", type=Path, default=None,
                        help="read-only repo root (default: the world)")
    parser.add_argument("--scratch", type=Path, default=None,
                        help="scratch root (default: world/.scientist/scratch)")
    parser.add_argument("--session", type=Path, default=None,
                        help="session dir (default: world/.scientist/session)")
    parser.add_argument("--probe", action="store_true",
                        help="one model call against the assembled context, "
                             "print the chosen actions, exit")
    args = parser.parse_args(argv)

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.probe:
        return _run_probe(spec, args)

    budget = dict(spec.get("budget") or {})
    roots = _resolve_roots(args, spec)
    episode_id = str(spec.get("episode_id") or "ep")

    world = LocalWorld(
        work=roots["work"], repo=roots["repo"], scratch=roots["scratch"],
        timeout_seconds=int(budget.get("command_timeout_seconds", 360)),
        cap_chars=int(budget.get("command_output_cap_chars", 12000)),
    )
    ledger_root = world.work / ".scientist"
    ledger_root.mkdir(parents=True, exist_ok=True)
    roots["scratch"].mkdir(parents=True, exist_ok=True)
    ledger = LocalLedger(ledger_root)
    assistant = InWorldAssistant(
        world=world, config=AssistantConfig.from_spec(spec),
        ledger=ledger, episode_id=episode_id,
        has_benchmark=bool(spec.get("editable_paths")),
    )
    session_dir = (
        args.session or ledger_root / "session"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    session = ScientistSession.load_or_create(
        session_dir, prompt_version=PROMPT_VERSION, episode_id=episode_id,
    )
    system_prompt = build_system_prompt(spec, roots=roots)
    model = build_stdlib_chat_model(dict(spec.get("model") or {}))
    # Resume: the wire log is the single source of truth for the
    # conversation. A run whose process died (crash, reboot, provider
    # change) resumes from it instead of restarting from zero — the
    # difference between a long-horizon process and a 3h toy.
    prior = session.load_wire_messages()
    if prior and not (ledger_root / "conclusion.json").exists():
        messages = prior
        resume_note = (
            "The research process was interrupted and has been resumed "
            "externally. The conversation record above is intact as it "
            "was left; the harness's wall clock restarted with this "
            "resume."
        )
        messages.append({"role": "user", "content": resume_note})
        session.append_wire({"role": "user", "content": resume_note})
        _compact_native(
            messages,
            keep_messages=int(budget.get("compact_keep_messages", 400)),
            max_chars=int(budget.get("compact_max_chars", 200_000)),
        )
        _upsert_judgment_message(messages, ledger.current_judgment())
        print(f"[scientist] resumed from wire.jsonl: "
              f"{len(prior)} messages replayed", flush=True)
    else:
        messages = _opening_messages(spec)
        _upsert_judgment_message(messages, ledger.current_judgment())

    try:
        result = run_episode(
            model=model,
            system_prompt=system_prompt,
            messages=messages,
            world=world,
            assistant=assistant,
            ledger=ledger,
            steps_budget=int(budget.get("steps", 200)),
            wall_seconds=float(budget.get("wall_seconds", 3600)),
            session=session,
            compact_keep_messages=int(
                budget.get("compact_keep_messages", 400)),
            compact_max_chars=int(
                budget.get("compact_max_chars", 200_000)),
        )
    except ModelError as exc:
        # Infra death (transport/model failure): fail loudly so a
        # scheduler retries the attempt rather than reading a silent
        # clean exit — but the exit contract is an INVARIANT: a crashed
        # run leaves a crashed conclusion, never nothing. Without this,
        # the harness cannot tell crash from still-running, and the
        # world's exit contract silently vanishes (observed twice in one
        # day: a dispatch crash and a provider-side 400).
        print(f"[scientist] model failure: {exc}", flush=True)
        _write_conclusion(ledger_root, {
            "outcome": "crashed",
            "conclusion": {"kind": "crashed",
                           "reason": f"model failure: {exc}"[:500]},
            "steps": 0, "actions": [],
        })
        return 1
    except Exception as exc:  # noqa: BLE001 — the contract must survive us
        print(f"[scientist] crashed: {exc!r}", flush=True)
        _write_conclusion(ledger_root, {
            "outcome": "crashed",
            "conclusion": {"kind": "crashed",
                           "reason": f"{type(exc).__name__}: {exc}"[:500]},
            "steps": 0, "actions": [],
        })
        return 1

    path = _write_conclusion(ledger_root, result)
    conclusion = result["conclusion"] or {}
    print(
        f"[scientist] concluded: {result['outcome']} after "
        f"{result['steps']} steps -> {path}",
        flush=True,
    )
    if conclusion.get("kind") == "deliver":
        handover = conclusion.get("handover") or {}
        print(f"[scientist] warning: "
              f"{str(handover.get('warning') or '')[:400]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
