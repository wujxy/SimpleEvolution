"""Worker CLI for one SimpleEvolution proposer episode.

Usage:
    python -m proposer.cli --manifest path/to/manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

from experiment.git_worktree import GitWorkspaceProvider, WorkspaceSpec

from .l2_memory import L2MemoryService
from .model import build_chat_model
from .orchestrator import ProposerOrchestrator
from .runtime import ApptainerRuntime, world_mount_map
from .scientist import ContextPolicy


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _unpack_snapshot(snapshot_ref: Path | None, session_dir: Path) -> None:
    """Unpack a snapshot tarball into the thread's session directory."""
    session_dir.mkdir(parents=True, exist_ok=True)
    if snapshot_ref is None or not snapshot_ref.exists():
        return
    with tarfile.open(snapshot_ref, "r:gz") as tar:
        tar.extractall(session_dir)


def _pack_snapshot(session_dir: Path, snapshot_ref: Path) -> None:
    """Pack the session directory into a snapshot tarball."""
    snapshot_ref.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot_ref, "w:gz") as tar:
        for item in session_dir.iterdir():
            tar.add(item, arcname=item.name)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


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
        "thread_id": result.thread_id,
        "node_id": result.node_id,
        "outcome": result.outcome,
        "proposals": proposals_with_meta,
        "abstain_reason": result.abstain_reason,
        "telemetry": result.deliberation_telemetry,
        "trace": result.trace,
    }


def _proposal_to_dict(proposal, proposal_id: str, snapshot_ref: Path) -> dict:
    """Serialize a ResearchProposal and attach SimpleEvolution identity fields."""
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
        "instruction": proposal.instruction,
        "rationale": {
            "research_target": target,
            "material_difference": proposal.material_difference,
        },
        "expectations": [],
        "falsifiers": [],
        "evidence_refs": list(proposal.evidence_refs),
        "snapshot_ref": str(snapshot_ref),
    }


def _pack_proposal_snapshots(
    session_dir: Path,
    proposals: tuple,
    thread_id: str,
    run_dir: Path,
    proposal_ids: list[str],
) -> list[dict]:
    """Create one immutable snapshot per proposal and return enriched proposal dicts.

    ``proposal_ids`` are issued by the Scheduler (single writer) when the
    proposer allocation was created.  The snapshot filename IS the proposal
    identity (§2.4): snapshot name == L2 proposal_id, so lineage audit and
    resume stay identity-first.
    """
    snapshot_dir = run_dir / "threads" / thread_id / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    if len(proposal_ids) < len(proposals):
        raise ValueError(
            f"reserved {len(proposal_ids)} proposal ids but produced "
            f"{len(proposals)} proposals"
        )
    enriched: list[dict] = []
    for proposal, proposal_id in zip(proposals, proposal_ids):
        snapshot_path = snapshot_dir / f"{proposal_id}.tgz"
        _pack_snapshot(session_dir, snapshot_path)
        enriched.append(_proposal_to_dict(proposal, proposal_id, snapshot_path))
    return enriched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proposer")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)

    manifest = _load_manifest(args.manifest)
    payload = manifest.get("payload", manifest)

    run_dir = Path(payload["run_dir"])
    thread_id = payload["thread_id"]
    node_id = payload["node_id"]
    node_sha = payload["node_sha"]
    proposal_ids = list(payload.get("proposal_ids", []))
    snapshot_ref_raw = payload.get("snapshot_ref") or ""
    snapshot_ref = Path(snapshot_ref_raw) if snapshot_ref_raw else None
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
        f"proposer-{thread_id}-{node_id[:8]}",
        node_sha,
    ))

    # Session handling: unpack snapshot into a temp session dir, run, repack.
    session_dir = run_dir / "threads" / thread_id / "session"
    if snapshot_ref is not None and snapshot_ref.exists():
        _unpack_snapshot(snapshot_ref, session_dir)

    memory_service = L2MemoryService(run_dir)
    runtime = ApptainerRuntime(
        image=runtime_image,
        binds=payload.get("runtime_binds", []),
        run_dir=run_dir,
    )
    researcher_cfg = payload.get("researcher", {})
    orchestrator = ProposerOrchestrator(
        model=build_chat_model(researcher_cfg),
        runtime=runtime,
        timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
        command_timeout_seconds=int(researcher_cfg.get("command_timeout_seconds", 120)),
        command_output_cap_chars=int(researcher_cfg.get("command_output_cap_chars", 12000)),
        context_policy=ContextPolicy.from_config(payload.get("context")),
    )

    status = "completed"
    error = None
    proposals_with_meta: list[dict] = []
    try:
        result = orchestrator.run_thread_episode(
            thread_id=thread_id,
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
            repo_path=repo_path,
            run_dir=run_dir,
            gate_block=gate_block,
            prompt_dir=Path(payload["prompt_dir"]) if payload.get("prompt_dir") else None,
            world_transition=world_transition,
            proposal_slots=proposal_slots,
            scientist_steps=scientist_steps,
        )
        proposals_with_meta = _pack_proposal_snapshots(
            session_dir, result.proposals, thread_id, run_dir, proposal_ids
        )
    except Exception as exc:
        status = "failed"
        error = str(exc)
        result = type("R", (), {
            "thread_id": thread_id,
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
        f"proposer-{thread_id}",
        role="proposer",
        identity={"thread_id": thread_id, "node_id": node_id},
        trace=result.trace,
        error=error,
    )

    result_path = Path(manifest.get("result_path", "result.json"))
    _atomic_write(
        result_path,
        {
            "protocol": manifest.get("protocol", "simpleevo.worker.v1"),
            "kind": "proposer",
            "request_id": manifest.get("request_id", thread_id),
            "status": status,
            "result": _result_to_dict(result, proposals_with_meta),
            "error": error,
            "execution": {
                "scheduler": "local",
                "job_id": None,
                "attempt": payload.get("attempt", 1),
                "host": "",
            },
        },
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
