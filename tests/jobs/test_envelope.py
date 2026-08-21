"""Tests for the worker wire envelope (simpleevo.jobs.envelope + worker CLIs)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from simpleevo.config import EvolutionConfig
from simpleevo.jobs.envelope import (
    WorkerResult,
    WorkerStatus,
    read_result,
    write_request,
    write_result,
)
from simpleevo.jobs.local import LocalSubmitter


# ---------------------------------------------------------------------------
# envelope.read_result robustness
# ---------------------------------------------------------------------------

def test_read_result_tolerates_missing_usage(tmp_path):
    # Legacy / telemetry-less workers omit the usage field entirely.
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "protocol": "simpleevo.worker.v1",
        "kind": "experiment",
        "request_id": "e1",
        "status": "completed",
        "result": {"outcome": "COMPLETED"},
        "error": None,
        "execution": {"scheduler": "local", "job_id": None, "attempt": 1, "host": ""},
    }), encoding="utf-8")
    res = read_result(result_path)
    assert res.status == WorkerStatus.COMPLETED
    assert res.usage == ()


def test_read_result_rejects_malformed_usage(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "protocol": "simpleevo.worker.v1",
        "kind": "experiment",
        "request_id": "e1",
        "status": "completed",
        "result": {},
        "usage": "not-a-list",  # wrong type is still an error
    }), encoding="utf-8")
    with pytest.raises(Exception):
        read_result(result_path)


def test_write_read_round_trip(tmp_path):
    path = tmp_path / "result.json"
    write_result(path, WorkerResult(
        kind="experiment",
        request_id="e1",
        status=WorkerStatus.COMPLETED,
        result={"outcome": "COMPLETED"},
        usage=({"tokens": 10},),
        error=None,
        execution={"scheduler": "condor", "job_id": "1.0", "attempt": 2, "host": "node"},
    ))
    res = read_result(path)
    assert res.kind == "experiment"
    assert res.request_id == "e1"
    assert res.status == WorkerStatus.COMPLETED
    assert res.execution["job_id"] == "1.0"
    assert res.execution["scheduler"] == "condor"
    assert len(res.usage) == 1


# ---------------------------------------------------------------------------
# worker CLIs write the standard envelope with backend metadata
# ---------------------------------------------------------------------------

def _run_experiment_worker(tmp_path, monkeypatch, argv) -> dict:
    """Run experiment.cli in-process against a manifest that infra-fails fast
    (repo path missing), then return the parsed result envelope."""
    from experiment import cli as experiment_cli

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    result_path = run_dir / "result.json"
    manifest_path = run_dir / "manifest.json"
    payload = {
        "experiment_id": "e1",
        "proposal_id": "p1",
        "parent_node_id": "n-root",
        "parent_sha": "abc123",
        "proposal": "no-op",
        "repo_path": str(tmp_path / "no-such-repo"),
        "run_dir": str(run_dir),
        "editable_paths": [],
        "frozen_paths": [],
        "eval_commands": ["true"],
        "metrics_schema": {"objective": {"key": "X", "lower_is_better": True}},
        "runtime_image": str(tmp_path / "no-such-image.sif"),
        "agent_timeout_seconds": 60,
        "eval_timeout_seconds": 60,
        "attempt": 1,
        "attempt_id": "at-1",
        "executor": {},
    }
    from simpleevo.jobs.envelope import WorkerRequest
    write_request(manifest_path, WorkerRequest(
        kind="experiment", request_id="e1", payload=payload, result_path=result_path))

    rc = experiment_cli.main(["--manifest", str(manifest_path), *argv])
    assert rc == 1  # infra failure -> non-zero exit, but a valid envelope
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_experiment_worker_writes_standard_envelope(tmp_path, monkeypatch):
    envelope = _run_experiment_worker(tmp_path, monkeypatch, ["--backend", "local"])
    assert envelope["status"] == "failed"
    assert envelope["result"]["outcome"] == "infra_failed"
    assert envelope["execution"]["scheduler"] == "local"
    assert envelope["execution"]["host"] != ""
    assert "usage" in envelope  # wire format is complete for read_result
    # read_result (the strict reader) accepts it.
    res = read_result(tmp_path / "run" / "result.json")
    assert res.status == WorkerStatus.FAILED


def test_experiment_worker_records_backend_job_id(tmp_path, monkeypatch):
    envelope = _run_experiment_worker(
        tmp_path, monkeypatch, ["--backend", "condor", "--job-id", "123.0"])
    assert envelope["execution"]["scheduler"] == "condor"
    assert envelope["execution"]["job_id"] == "123.0"
    assert envelope["execution"]["host"] != ""


# ---------------------------------------------------------------------------
# Local vs Condor manifest parity (interface standardization)
# ---------------------------------------------------------------------------

def test_local_and_condor_stage_identical_manifests(tmp_path, monkeypatch):
    """For the same payload, both backends write byte-identical manifests at
    the same canonical path — the Scheduler cannot tell them apart."""
    from simpleevo.jobs.condor import HTCondorSubmitter

    payload = {
        "experiment_id": "exp-1",
        "proposal_id": "p1",
        "parent_node_id": "n-root",
        "parent_sha": "abc123",
        "proposal": "no-op",
    }
    raw = {
        "goal": "g", "repo_path": "/repo", "runtime_image": "/img.sif",
        "eval_commands": ["echo X=1"],
        "metrics_schema": {"objective": {"key": "X", "lower_is_better": True}},
        "axes": ["X"],
        "jobs": {"backend": "condor"},
    }
    config = EvolutionConfig.from_dict(raw)

    def fake_popen(*a, **k):
        return type("P", (), {"poll": lambda self: None})()

    # Both backends target the SAME run_dir (that is the whole point of the
    # parity: the Scheduler cannot tell which backend produced a manifest).
    run_dir = tmp_path / "run"
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    local = LocalSubmitter(run_dir, config)
    local.submit_experiment("exp-1", payload)
    local_manifest = (run_dir / "experiments" / "exp-1" / "manifest.json").read_bytes()

    condor = HTCondorSubmitter(run_dir, config, python="/usr/bin/python3")
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(
        argv, 0, stdout="1 job(s) submitted to cluster 1.", stderr=""))
    condor.submit_experiment("exp-1", payload)
    condor_manifest = (run_dir / "experiments" / "exp-1" / "manifest.json").read_bytes()
    assert local_manifest == condor_manifest
