"""Worker CLI for one SimpleEvolution proposer episode
(host-side: the old JSON-protocol production path; frozen until the
oneworld path passes its container smoke, then deleted in one cut).

Usage:
    python -m simpleevo.jobs.proposer_worker --manifest path/to/manifest.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
from pathlib import Path

from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import load_generator_basis
from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result

from scientist.assistant.git_worktree import GitWorkspaceProvider

from scientist.memory.l2 import L2MemoryService
from scientist.host.wake import build_wake_view
from scientist.model import build_chat_model
from scientist.host.orchestrator import ProposerOrchestrator
from scientist.host.runtime import ApptainerRuntime, world_mount_map
from scientist.host.scientist import ContextPolicy


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _inherit_parent_session(
    run_dir: Path,
    inherited_from_episode_id: str | None,
    session_dir: Path,
    *,
    first_layer_seed: dict | None = None,
) -> None:
    """Copy the parent episode's final cognition into this episode's session.

    Same-Node reseeds may start from the parent episode's persisted session.
    Delivery-produced Child Nodes instead use ``first_layer_seed`` and skip
    this copy so sibling trajectory cannot leak in. Only run on first entry —
    a crash retry resumes the current Episode (Resume ≠ Evolution).
    """
    if not inherited_from_episode_id or first_layer_seed:
        return
    parent_session_dir = run_dir / "episodes" / inherited_from_episode_id / "session"
    if not parent_session_dir.is_dir():
        return
    session_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(parent_session_dir, session_dir, dirs_exist_ok=True)


def _write_l1_trace(
    run_dir: Path,
    invocation_id: str,
    *,
    role: str,
    identity: dict[str, str | None],
    trace: dict | None,
    error: str | None,
) -> None:
    """Write the proposer's deliberation trace into the L1 store."""
    try:
        from simpleevo.trace.store import TraceStore

        store = TraceStore(run_dir)
        store.start_invocation(invocation_id, role=role, identity=identity)
        store.append_event(
            invocation_id,
            "proposer_result",
            {"trace": trace or {}, "error": error},
            identity=identity,
        )
    except Exception as exc:
        print(f"[trace] proposer L1 write failed: {exc}", flush=True)


def _result_to_dict(result, *, world_sha: str | None = None) -> dict:
    conclusion = dict(getattr(result, "conclusion", None) or {})
    # The worker result carries the seat's conclusion; the delivery's
    # world_sha is snapshotted HERE by the harness (the seat cannot choose
    # a different SHA than its laboratory's current state).
    if conclusion.get("kind") == "deliver":
        conclusion["world_sha"] = world_sha
        conclusion.setdefault("node_id", result.node_id)
        conclusion.setdefault("episode_id", result.episode_id)
    else:
        conclusion.setdefault("node_id", result.node_id)
        conclusion.setdefault("episode_id", result.episode_id)
    return {
        "episode_id": result.episode_id,
        "node_id": result.node_id,
        "outcome": result.outcome,
        "conclusion": conclusion,
        "abstain_reason": result.abstain_reason,
        "telemetry": result.deliberation_telemetry,
        "trace": result.trace,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proposer")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--job-id", default=None,
                        help="backend job id (condor cluster.proc); None for local")
    parser.add_argument("--backend", default="local",
                        help="scheduler backend: local | condor")
    args = parser.parse_args(argv)

    manifest = _load_manifest(args.manifest)
    payload = manifest.get("payload", manifest)

    run_dir = Path(payload["run_dir"])
    episode_id = payload["episode_id"]
    node_id = payload["node_id"]
    proposal_ids = list(payload.get("proposal_ids", []))
    attempt_id = str(payload.get("attempt_id", ""))
    attempt = int(payload.get("attempt", 1))
    goal = payload["goal"]
    editable = list(payload.get("editable_paths", []))
    gate_block = payload.get("gate_block", "")
    proposal_slots = int(payload.get("proposal_slots", 1))
    scientist_steps = int(payload.get("scientist_steps", 200))
    runtime_image = Path(payload["runtime_image"])
    repo_path = Path(payload["repo_path"])

    # Wake-time worldview assembly (module contract §3): the envelope
    # carried IDs; every dynamic fact below is read from the store NOW.
    wake_view = build_wake_view(
        ResearchQueries(run_dir / "simpleevo.db"),
        load_generator_basis(),
        node_id=node_id,
        episode_id=episode_id,
    )
    node_sha = wake_view["node_sha"]
    inherited_from_episode_id = (
        wake_view["inherited_from_episode_id"] or None)
    seat = wake_view["seat"]
    first_layer_seed = wake_view.get("first_layer") or {}
    world_transition = wake_view.get("world_transition") or {}
    adjudication_feedback = wake_view.get("adjudication_feedback")

    # Materialize the lease's LABORATORY at the exact node SHA (§9): one
    # persistent writable world for the whole lease.  The seat's own shell
    # and every work() call share it; a reopen resumes it.  Unlike the old
    # read-only proposer workspace, the lab is NEVER dropped here — the
    # delivered side-chain SHAs live in the clone's object store precisely
    # so the adjudication worker can evaluate them.
    provider = GitWorkspaceProvider(run_dir, repo_path)
    provider.initialize()
    from scientist.assistant.lab import Laboratory

    lab = Laboratory(
        provider=provider, episode_id=episode_id, node_sha=node_sha,
        editable_paths=tuple(editable),
    )
    workspace = lab.main()

    # Session handling: inherit the parent episode's final cognition only on
    # FIRST entry (no lived trajectory yet).  A crash-retry of the same episode
    # resumes the already-persisted session instead of re-inheriting (Resume ≠
    # Evolution).
    session_dir = run_dir / "episodes" / episode_id / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    if not (session_dir / "session.jsonl").exists():
        _inherit_parent_session(
            run_dir,
            inherited_from_episode_id,
            session_dir,
            first_layer_seed=first_layer_seed,
        )

    memory_service = L2MemoryService(run_dir)
    runtime = ApptainerRuntime(
        image=runtime_image,
        binds=payload.get("runtime_binds", []),
        run_dir=run_dir,
    )
    researcher_cfg = payload.get("researcher", {})
    from simpleevo.trace.usage import UsageRecorder

    usage_recorder = UsageRecorder(run_dir)
    db_path = run_dir / "simpleevo.db"
    lease_id = str(payload.get("allocation_id") or "")

    # Guarantee the lease's research-state head row exists from the first
    # breath: the generalized exit guard (≥1 state before ANY conclusion)
    # then always has something on file, and a crash mid-attempt cannot
    # evaporate the investigation.
    from simpleevo.db.lease_writer import upsert_lease_research_state

    if lease_id:
        try:
            upsert_lease_research_state(
                db_path, lease_id=lease_id, episode_id=episode_id,
                node_id=node_id,
                working_model="(lease opened; no model registered yet)",
            )
        except Exception as exc:
            print(f"[proposer] initial state upsert failed: {exc}",
                  flush=True)

    # The seat's claude assistant (consult/work) — the two hands.
    from scientist.assistant.hands import AssistantHands, HandTally

    hands = AssistantHands(
        run_dir=run_dir, db_path=db_path, lease_id=lease_id,
        episode_id=episode_id, node_id=node_id, node_sha=node_sha,
        lens=(seat or {}).get("lens_id"),
        lab=lab, runtime_image=runtime_image,
        executor_cfg=dict(payload.get("executor", {})),
        editable_paths=tuple(editable),
        read_only_binds=tuple(payload.get("read_only_binds", [])),
        tally=HandTally(
            max_consult_calls=payload.get("lease_max_consult_calls"),
            max_work_calls=payload.get("lease_max_work_calls"),
        ),
        usage_observer=lambda usage, call_id: usage_recorder.record(
            "assistant", usage, work_id=call_id),
        attempt_id=attempt_id or None,
    )

    orchestrator = ProposerOrchestrator(
        model=build_chat_model(researcher_cfg),
        runtime=runtime,
        timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
        command_timeout_seconds=int(researcher_cfg.get("command_timeout_seconds", 120)),
        command_output_cap_chars=int(researcher_cfg.get("command_output_cap_chars", 12000)),
        usage_observer=lambda usage: usage_recorder.record(
            "proposer", usage, work_id=attempt_id or None,
            lease_id=lease_id or None),
        context_policy=ContextPolicy.from_config(payload.get("context")),
        hands=hands,
    )

    status = "completed"
    error = None
    world_sha: str | None = None
    try:
        result = orchestrator.run_episode(
            episode_id=episode_id,
            node_id=node_id,
            node_sha=node_sha,
            workspace=workspace.path,
            goal=goal,
            editable=editable,
            frozen=[],
            world_mount=world_mount_map({
                "editable_paths": editable,
                "read_only_binds": payload.get("read_only_binds", []),
            }),
            memory_service=memory_service,
            # The Scientist's research commands validate the workspace's
            # worktree gitdir against this repo's ``.git/worktrees``. That
            # metadata lives in the CLONE GitWorkspaceProvider made
            # (``run_dir/repo``), not the source repo — pass the clone, the
            # way the experiment worker does via ``.repo``.
            repo_path=provider.repo,
            run_dir=run_dir,
            gate_block=gate_block,
            prompt_dir=Path(payload["prompt_dir"]) if payload.get("prompt_dir") else None,
            first_layer_seed=first_layer_seed,
            world_transition=world_transition,
            lens=seat,
            proposal_slots=proposal_slots,
            scientist_steps=scientist_steps,
            adjudication_feedback=adjudication_feedback,
        )
        if result.outcome == "error":
            # The orchestrator converts a research crash (API/network/protocol
            # failure) into outcome="error" rather than raising.  Treat it as
            # infra: report status="failed" so the Scheduler marks the Attempt
            # failed and keeps the allocation open for retry (§16/§17) — never
            # as a clean "completed" abstention that would close the allocation.
            raise RuntimeError(
                result.abstain_reason or "proposer episode errored")
        if (result.conclusion or {}).get("kind") == "deliver":
            # The delivery IS the laboratory's current state: the harness
            # snapshots it mechanically at the moment of delivery.
            world_sha = lab.snapshot("deliver")
            if world_sha is None:
                # No editable-path change since the node — an empty
                # delivery is not a world.  Refuse like the scheduler
                # would, with the reason.
                raise RuntimeError(
                    "deliver_world with an unchanged laboratory: the "
                    "delivered world must differ from the purchased node"
                )
    except Exception as exc:
        status = "failed"
        error = str(exc)
        result = type("R", (), {
            "episode_id": episode_id,
            "node_id": node_id,
            "outcome": "error",
            "conclusion": None,
            "abstain_reason": str(exc),
            "deliberation_telemetry": {},
            "trace": {},
        })()
    # NOTE: the lab worktree is deliberately NOT removed — it is the
    # lease's persistent world; a reopen or adjudication resumes it.

    _write_l1_trace(
        run_dir,
        f"proposer-{attempt_id}" if attempt_id else f"proposer-{episode_id}",
        role="proposer",
        identity={
            "episode_id": episode_id,
            "node_id": node_id,
            "attempt_id": attempt_id or None,
            "attempt": str(attempt),
        },
        trace=result.trace,
        error=error,
    )

    result_path = Path(manifest.get("result_path", "result.json"))
    execution = {
        "scheduler": args.backend,
        "job_id": args.job_id,
        "attempt": attempt,
        "host": socket.gethostname(),
    }
    write_result(
        result_path,
        WorkerResult(
            kind="proposer",
            request_id=manifest.get("request_id", episode_id),
            status=WorkerStatus.COMPLETED if status == "completed" else WorkerStatus.FAILED,
            result=_result_to_dict(result, world_sha=world_sha),
            usage=(),
            error=error,
            execution=execution,
        ),
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
