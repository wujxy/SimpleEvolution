"""Worker CLI for one SimpleEvolution proposer episode.

Usage:
    python -m proposer.cli --manifest path/to/manifest.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
from pathlib import Path

from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result
from simpleevo.research_state import (
    research_state_to_dict,
    transformation_to_dict,
)

from experiment.git_worktree import GitWorkspaceProvider, WorkspaceSpec

from .l2_memory import L2MemoryService
from .model import build_chat_model
from .orchestrator import ProposerOrchestrator
from .runtime import ApptainerRuntime, world_mount_map
from .scientist import ContextPolicy


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _inherit_parent_session(
    run_dir: Path,
    inherited_from_episode_id: str | None,
    session_dir: Path,
    *,
    research_state_seed: dict | None = None,
) -> None:
    """Copy the parent episode's final cognition into this episode's session.

    Same-Node reseeds may start from the parent episode's persisted session.
    Proposal-produced Child Nodes instead use ``research_state_seed`` and skip
    this copy so sibling trajectory cannot leak in. Only run on first entry —
    a crash retry resumes the current Episode (Resume ≠ Evolution).
    """
    if (
        not inherited_from_episode_id
        or (research_state_seed or {}).get("originating_research_state")
    ):
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


def _result_to_dict(result, proposals_with_meta) -> dict:
    return {
        "episode_id": result.episode_id,
        "node_id": result.node_id,
        "outcome": result.outcome,
        "proposals": proposals_with_meta,
        "research_states": [
            research_state_to_dict(item)
            for item in getattr(result, "research_states", ())
        ],
        "transformations": [
            transformation_to_dict(item)
            for item in getattr(result, "transformations", ())
        ],
        "abstain_reason": result.abstain_reason,
        "telemetry": result.deliberation_telemetry,
        "trace": result.trace,
    }


def _proposal_to_dict(proposal, proposal_id: str) -> dict:
    """Serialize a ResearchProposal and attach its L2 identity (proposal_id)."""
    target: dict = {}
    rt = proposal.research_target
    if hasattr(rt, "finding_id"):
        target = {"kind": "existing_finding", "finding_id": rt.finding_id}
    else:
        target = {
            "kind": "new_finding",
            "question": rt.question,
            "mechanisms": list(rt.mechanisms),
            "code_regions": list(rt.code_regions),
        }
    return {
        "proposal_id": proposal_id,
        "research_state_id": proposal.research_state_id,
        "instruction": proposal.instruction,
        "rationale": {
            "research_target": target,
            "expectation": proposal.expectation,
            "material_difference": proposal.material_difference,
        },
        "expectations": [],
        "falsifiers": [],
        "evidence_refs": list(proposal.evidence_refs),
    }


def _enrich_proposals(proposals: tuple, proposal_ids: list[str]) -> list[dict]:
    """Attach the Scheduler-issued proposal_ids to the proposals."""
    if len(proposal_ids) < len(proposals):
        raise ValueError(
            f"reserved {len(proposal_ids)} proposal ids but produced "
            f"{len(proposals)} proposals"
        )
    return [
        _proposal_to_dict(proposal, proposal_id)
        for proposal, proposal_id in zip(proposals, proposal_ids)
    ]


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
    node_sha = payload["node_sha"]
    proposal_ids = list(payload.get("proposal_ids", []))
    attempt_id = str(payload.get("attempt_id", ""))
    attempt = int(payload.get("attempt", 1))
    inherited_from_episode_id = payload.get("inherited_from_episode_id") or None
    generator_basis = payload.get("generator_basis")
    suggested_operator_id = payload.get("suggested_operator_id") or None
    research_state_seed = payload.get("research_state_seed") or {}
    world_transition = payload.get("world_transition") or {}
    goal = payload["goal"]
    editable = list(payload.get("editable_paths", []))
    gate_block = payload.get("gate_block", "")
    proposal_slots = int(payload.get("proposal_slots", 1))
    scientist_steps = int(payload.get("scientist_steps", 200))
    runtime_image = Path(payload["runtime_image"])
    repo_path = Path(payload["repo_path"])

    # Materialize the Node World at the exact node SHA (§9).  The Scientist
    # must study its own world, not the repo's current checkout.  This mirrors
    # the Executor's GitWorkspaceProvider but for read-only investigation.
    provider = GitWorkspaceProvider(run_dir, repo_path)
    provider.initialize()
    workspace = provider.create(WorkspaceSpec(
        f"proposer-{episode_id}-{node_id[:8]}",
        node_sha,
    ))

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
            research_state_seed=research_state_seed,
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
    orchestrator = ProposerOrchestrator(
        model=build_chat_model(researcher_cfg),
        runtime=runtime,
        timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
        command_timeout_seconds=int(researcher_cfg.get("command_timeout_seconds", 120)),
        command_output_cap_chars=int(researcher_cfg.get("command_output_cap_chars", 12000)),
        usage_observer=lambda usage: usage_recorder.record("proposer", usage),
        context_policy=ContextPolicy.from_config(payload.get("context")),
    )

    status = "completed"
    error = None
    proposals_with_meta: list[dict] = []
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
            research_state_seed=research_state_seed,
            world_transition=world_transition,
            generator_basis=generator_basis,
            suggested_operator_id=suggested_operator_id,
            proposal_slots=proposal_slots,
            scientist_steps=scientist_steps,
        )
        if result.outcome == "error":
            # The orchestrator converts a research crash (API/network/protocol
            # failure) into outcome="error" rather than raising.  Treat it as
            # infra: report status="failed" so the Scheduler marks the Attempt
            # failed and keeps the allocation open for retry (§16/§17) — never
            # as a clean "completed" abstention that would close the allocation.
            raise RuntimeError(
                result.abstain_reason or "proposer episode errored")
        proposals_with_meta = _enrich_proposals(
            result.proposals, proposal_ids)
    except Exception as exc:
        status = "failed"
        error = str(exc)
        result = type("R", (), {
            "episode_id": episode_id,
            "node_id": node_id,
            "outcome": "error",
            "proposals": (),
            "abstain_reason": str(exc),
            "deliberation_telemetry": {},
            "trace": {},
        })()
    finally:
        provider.remove(workspace)

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
            result=_result_to_dict(result, proposals_with_meta),
            usage=(),
            error=error,
            execution=execution,
        ),
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
