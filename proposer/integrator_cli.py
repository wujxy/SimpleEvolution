"""Worker entry point for a temporary Integrator request."""
from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result

from .integrator import IntegratorAgent
from .model import build_chat_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="integrator")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--backend", default="local")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    payload = manifest["payload"]
    error = None
    result = None
    try:
        result = IntegratorAgent(
            model=build_chat_model(payload.get("researcher", {})),
            timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
            max_steps=int(payload.get("integrator_steps", 4)),
        ).integrate(
            payload["request"],
            public_evidence=payload.get("public_evidence", {}),
            session_dir=args.manifest.parent / "session",
        )
    except Exception as exc:
        error = str(exc)

    output = {}
    if result is not None:
        output = {
            "outcome": result.outcome,
            "reason": result.reason,
            "proposal": None if result.outcome != "submitted" else {
                "proposal_id": payload["proposal_id"],
                "research_state_id": f"rs-{payload['episode_id']}-integration",
                "instruction": result.instruction,
                "rationale": result.rationale,
                "research_operation": "synthesize",
                "donor_experiment_ids": list(result.donor_experiment_ids),
                "evidence_refs": list(result.evidence_refs),
            },
            "research_state": None if result.outcome != "submitted" else {
                "research_state_id": f"rs-{payload['episode_id']}-integration",
                "node_id": payload["request"]["target_node_id"],
                "episode_id": payload["episode_id"],
                "working_model": result.working_model,
                "evidence_refs": list(result.evidence_refs),
            },
        }
    write_result(manifest["result_path"], WorkerResult(
        kind="integrator", request_id=manifest["request_id"],
        status=WorkerStatus.COMPLETED if result is not None else WorkerStatus.FAILED,
        result=output, error=error,
        execution={"scheduler": args.backend, "job_id": args.job_id,
                   "host": socket.gethostname()},
    ))
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
