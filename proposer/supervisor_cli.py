"""Worker entry point for one persistent Supervisor wake-up turn."""
from __future__ import annotations

import argparse
import json
import socket
import uuid
from pathlib import Path

from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result

from .l2_memory import L2MemoryService
from .model import build_chat_model
from .scientist import ContextPolicy
from .supervisor import SupervisorAgent, SupervisorTools, load_supervisor_session


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
    result = None
    try:
        run_dir = Path(payload["run_dir"])
        memory = L2MemoryService(run_dir)
        session = load_supervisor_session(run_dir)
        agent = SupervisorAgent(
            model=build_chat_model(payload.get("researcher", {})),
            timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
            max_steps=int(payload.get("supervisor_steps", 40)),
            context_policy=ContextPolicy.from_config(
                payload.get("context") or {}),
        )
        turn = agent.resume(
            session=session,
            tools=SupervisorTools(
                memory, runtime_facts=payload.get("runtime_facts") or {}),
            batch=payload["batch"],
            run_context=payload.get("run_context"),
        )
        cursor_to = int(payload["batch"]["event_batch"]["cursor_to"])
        # Audit mirror only; the authoritative cursor lives in the store and
        # advances inside the scheduler's decision-commit transaction.
        session.meta["event_cursor"] = cursor_to
        session.save_meta()
        result = {
            "decision_id": payload.get("decision_id") or uuid.uuid4().hex,
            "work_id": manifest["request_id"],
            "decision_kind": turn.decision_kind,
            "seat_purchases": [
                {"node_id": node_id, "lens": lens}
                for node_id, lens in turn.seat_purchases
            ],
            "rationale": turn.rationale,
            "detail": dict(turn.detail or {}),
            "event_cursor_to": cursor_to,
        }
    except Exception as exc:
        error = str(exc)
    write_result(result_path, WorkerResult(
        kind="supervisor",
        request_id=manifest["request_id"],
        status=WorkerStatus.COMPLETED if result is not None else WorkerStatus.FAILED,
        result={} if result is None else result,
        error=error,
        execution={"scheduler": args.backend, "job_id": args.job_id,
                   "host": socket.gethostname()},
    ))
    return 0 if result is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
