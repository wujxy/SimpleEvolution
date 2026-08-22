"""Worker entry point for one stateless Supervisor decision."""
from __future__ import annotations

import argparse
import json
import socket
from dataclasses import asdict
from pathlib import Path

from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result

from .model import build_chat_model
from .supervisor import GroupSnapshot, SnapshotNode, SupervisorAgent


def _snapshot(raw: dict) -> GroupSnapshot:
    return GroupSnapshot(
        epoch_id=str(raw["epoch_id"]),
        epoch_root_node_id=str(raw["epoch_root_node_id"]),
        watermark=str(raw["watermark"]),
        eligible_nodes=tuple(SnapshotNode(**item) for item in raw["eligible_nodes"]),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="supervisor")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--job-id", default=None)
    parser.add_argument("--backend", default="local")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    payload = manifest["payload"]
    result_path = Path(manifest["result_path"])
    error = None
    decision = None
    try:
        agent = SupervisorAgent(
            model=build_chat_model(payload.get("researcher", {})),
            timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
            max_steps=int(payload.get("supervisor_steps", 3)),
        )
        decision = agent.decide(
            _snapshot(payload["snapshot"]),
            proposer_capacity=int(payload["proposer_capacity"]),
            session_dir=args.manifest.parent / "session",
        )
    except Exception as exc:
        error = str(exc)
    write_result(result_path, WorkerResult(
        kind="supervisor",
        request_id=manifest["request_id"],
        status=WorkerStatus.COMPLETED if decision is not None else WorkerStatus.FAILED,
        result={} if decision is None else asdict(decision),
        error=error,
        execution={"scheduler": args.backend, "job_id": args.job_id,
                   "host": socket.gethostname()},
    ))
    return 0 if decision is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
