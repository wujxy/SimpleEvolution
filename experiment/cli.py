"""Worker CLI for one experiment job.

Usage:
    python -m experiment.cli --manifest path/to/manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .contracts import ExecutionResult, ExperimentRequest, ExperimentResult, GateDecision, GateResult
from .runner import ExperimentRunner


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_request(payload: dict) -> ExperimentRequest:
    return ExperimentRequest(
        experiment_id=str(payload["experiment_id"]),
        proposal_id=str(payload["proposal_id"]),
        parent_node_id=str(payload["parent_node_id"]),
        parent_sha=str(payload["parent_sha"]),
        proposal=str(payload["proposal"]),
        repo_path=Path(payload["repo_path"]),
        run_dir=Path(payload["run_dir"]),
        editable_paths=tuple(payload.get("editable_paths", [])),
        frozen_paths=tuple(payload.get("frozen_paths", [])),
        eval_commands=tuple(payload.get("eval_commands", [])),
        metrics_schema=dict(payload.get("metrics_schema", {})),
        runtime_image=Path(payload["runtime_image"]),
        agent_timeout_seconds=int(payload.get("agent_timeout_seconds", 3600)),
        eval_timeout_seconds=int(payload.get("eval_timeout_seconds", 600)),
        attempt=int(payload.get("attempt", 1)),
    )


def _result_to_dict(result: ExperimentResult) -> dict:
    return {
        "experiment_id": result.experiment_id,
        "proposal_id": result.proposal_id,
        "parent_node_id": result.parent_node_id,
        "parent_sha": result.parent_sha,
        "status": result.status,
        "sha": result.sha,
        "metrics": dict(result.metrics),
        "gate": {
            "passed": result.gate.passed,
            "results": {
                name: {"passed": gr.passed, "detail": gr.detail}
                for name, gr in result.gate.results.items()
            },
        },
        "eval_block": result.eval_block,
        "changed_paths": [p.as_posix() for p in result.changed_paths],
        "execution": {
            "status": result.execution.status,
            "reason": result.execution.reason,
            "self_report": dict(result.execution.self_report)
            if result.execution.self_report is not None else None,
        },
    }


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args(argv)

    manifest = _load_manifest(args.manifest)
    request = _build_request(manifest.get("payload", manifest))

    try:
        runner = ExperimentRunner(request)
        result = runner.run()
        status = "completed"
        error = None
    except Exception as exc:
        result = ExperimentResult(
            experiment_id=request.experiment_id,
            proposal_id=request.proposal_id,
            parent_node_id=request.parent_node_id,
            parent_sha=request.parent_sha,
            status="WORKER_FAILED",
            sha=None,
            metrics={},
            gate=GateDecision({}, False),
            eval_block=str(exc),
            changed_paths=(),
            execution=ExecutionResult(
                status="WORKER_FAILED",
                reason=str(exc),
                output="",
                self_report=None,
            ),
        )
        status = "failed"
        error = str(exc)

    result_path = Path(manifest.get("result_path", "result.json"))
    _atomic_write(
        result_path,
        {
            "protocol": manifest.get("protocol", "simpleevo.worker.v1"),
            "kind": "experiment",
            "request_id": manifest.get("request_id", request.experiment_id),
            "status": status,
            "result": _result_to_dict(result),
            "error": error,
            "execution": {
                "scheduler": "local",
                "job_id": None,
                "attempt": request.attempt,
                "host": "",
            },
        },
    )
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
