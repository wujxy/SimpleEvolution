"""Tests for the HTCondor submitter (simpleevo.jobs.condor).

condor_submit / condor_q / condor_rm are mocked so these run on any machine.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from simpleevo.config import EvolutionConfig
from simpleevo.jobs.condor import HTCondorSubmitter


def _config(**jobs) -> EvolutionConfig:
    raw = {
        "goal": "g",
        "repo_path": "/repo",
        "runtime_image": "/img.sif",
        "eval_commands": ["echo X=1"],
        "metrics_schema": {"objective": {"key": "X", "lower_is_better": True}},
        "axes": ["X"],
        "jobs": {
            "backend": "condor",
            "collector": "cm01.ihep.ac.cn",
            "schedd_name": "scheduler@schedd12.ihep.ac.cn",
            "accounting_group": "JUNO.juno.default",
            "accounting_group_user": "lidian",
            "cpu_model": "zen5",
            "machine_constraint": 'Machine == "lhws316.ihep.ac.cn"',
            "memory_mb": 4096,
            "cpus": 2,
            **jobs,
        },
    }
    return EvolutionConfig.from_dict(raw)


def _fake_submit(monkeypatch, *, rc=0, stdout="1 job(s) submitted to cluster 987654.",
                 stderr=""):
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _submit_experiment(submitter, eid="exp-1"):
    return submitter.submit_experiment(eid, {
        "experiment_id": eid,
        "proposal_id": "p1",
        "parent_node_id": "n-root",
        "parent_sha": "abc123",
        "proposal": "no-op",
    })


def test_submit_stages_job_files_and_parses_cluster_id(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    calls = _fake_submit(monkeypatch)

    result_path = _submit_experiment(submitter)

    exp_dir = tmp_path / "experiments" / "exp-1"
    assert result_path == str(exp_dir / "result.json")

    # Manifest staged at the SAME layout Local uses (interface parity).
    manifest = json.loads((exp_dir / "manifest.json").read_text())
    assert manifest["kind"] == "experiment"
    assert manifest["payload"]["experiment_id"] == "exp-1"
    assert manifest["result_path"] == str(exp_dir / "result.json")

    # job.sh sources job_env.sh and runs the worker with --job-id from $1.
    job_sh = (exp_dir / "job.sh").read_text()
    assert "source" in job_sh and "job_env.sh" in job_sh
    assert "-m scientist.assistant.cli" in job_sh
    assert "--backend condor" in job_sh
    assert '--job-id "$1"' in job_sh

    # job.sub carries the condor knobs.
    job_sub = (exp_dir / "job.sub").read_text()
    assert "universe = vanilla" in job_sub
    assert "accounting_group = JUNO.juno.default" in job_sub
    assert "accounting_group_user = lidian" in job_sub
    assert '+HepJob_RequestOS = "AlmaLinux9"' in job_sub
    assert '+IHEP_RealGroup = "juno"' in job_sub
    assert 'Requirements = CpuFamily==26 && CpuModelNumber==2 && Machine == "lhws316.ihep.ac.cn"' in job_sub
    assert "request_memory = 4096" in job_sub
    assert "request_cpus = 2" in job_sub

    # condor_submit was called with -pool/-name targeting the JUNO schedd.
    assert calls["argv"][0] == "condor_submit"
    assert "-pool" in calls["argv"] and "cm01.ihep.ac.cn" in calls["argv"]
    assert "scheduler@schedd12.ihep.ac.cn" in calls["argv"]

    # Ledger records the parsed cluster id.
    ledger = json.loads((tmp_path / "jobs.json").read_text())
    assert ledger["experiment"]["exp-1"]["job_id"] == "987654.0"


def test_job_env_injects_configured_proxy(tmp_path, monkeypatch):
    # A configured jobs.*_proxy is written into run_dir/job_env.sh so execute
    # nodes route external model/API traffic through the jump host.
    submitter = HTCondorSubmitter(tmp_path, _config(
        https_proxy="http://192.168.237.165:3128",
        no_proxy="localhost,127.0.0.1,.ihep.ac.cn",
    ), python="/usr/bin/python3")
    job_env = (tmp_path / "job_env.sh").read_text(encoding="utf-8")
    assert "export HTTPS_PROXY=http://192.168.237.165:3128" in job_env
    assert "export https_proxy=http://192.168.237.165:3128" in job_env
    assert "export NO_PROXY=localhost,127.0.0.1,.ihep.ac.cn" in job_env
    assert "export no_proxy=localhost,127.0.0.1,.ihep.ac.cn" in job_env


def test_job_env_without_proxy_config_has_no_proxy_lines(tmp_path, monkeypatch):
    # Regression: no proxy configured -> nothing injected (forwarded env only).
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    job_env = (tmp_path / "job_env.sh").read_text(encoding="utf-8")
    assert "PROXY" not in job_env


def test_submit_raises_on_condor_failure(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch, rc=1, stdout="", stderr="condor_submit: permission denied")
    with pytest.raises(RuntimeError, match="condor submit failed"):
        _submit_experiment(submitter)


def test_probe_job_states(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch)
    _submit_experiment(submitter)

    # running (idle=1 / running=2) -> "running"
    monkeypatch.setattr(submitter, "_query_statuses", lambda ids: {"987654.0": 1})
    assert submitter.probe_job("exp-1", "experiment") == "running"
    monkeypatch.setattr(submitter, "_query_statuses", lambda ids: {"987654.0": 2})
    assert submitter.probe_job("exp-1", "experiment") == "running"

    # held -> "held"
    monkeypatch.setattr(submitter, "_query_statuses", lambda ids: {"987654.0": 5})
    assert submitter.probe_job("exp-1", "experiment") == "held"

    # query failure -> "unknown" (never "gone")
    monkeypatch.setattr(submitter, "_query_statuses", lambda ids: None)
    assert submitter.probe_job("exp-1", "experiment") == "unknown"

    # untracked work -> "unknown"
    assert submitter.probe_job("never-submitted", "experiment") == "unknown"


def test_probe_job_gone_requires_grace(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    submitter.job_cfg = _config(disappearance_grace_seconds=0).jobs
    _fake_submit(monkeypatch)
    _submit_experiment(submitter)

    # Job absent from the queue: first probe starts the grace window...
    monkeypatch.setattr(submitter, "_query_statuses", lambda ids: {})
    assert submitter.probe_job("exp-1", "experiment") == "unknown"
    # ...once the window elapses it is declared gone.
    submitter._ledger["experiment"]["exp-1"]["gone_since"] -= 1
    assert submitter.probe_job("exp-1", "experiment") == "gone"


def test_remove_job_drops_ledger_and_calls_condor_rm(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch)
    _submit_experiment(submitter)
    rm_calls = []

    def fake_run(argv, **kwargs):
        if argv[0] == "condor_submit":
            return subprocess.CompletedProcess(argv, 0,
                                               stdout="1 job(s) submitted to cluster 1.", stderr="")
        rm_calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    submitter.remove_job("exp-1", "experiment")
    assert rm_calls and "condor_rm" in rm_calls[0] and "987654.0" in rm_calls[0]
    ledger = json.loads((tmp_path / "jobs.json").read_text())
    assert "exp-1" not in ledger.get("experiment", {})


def test_proposer_and_experiment_share_layout(tmp_path, monkeypatch):
    """Interface parity: both kinds stage at the canonical run_dir layout."""
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch)
    submitter.submit_experiment("exp-9", {
        "experiment_id": "exp-9", "proposal_id": "p", "parent_node_id": "n",
        "parent_sha": "s", "proposal": "x"})
    submitter.submit_proposer("alloc-9", {
        "allocation_id": "alloc-9", "node_id": "n", "node_sha": "s",
        "episode_id": "e", "proposal_ids": []})
    assert (tmp_path / "experiments" / "exp-9" / "manifest.json").exists()
    assert (tmp_path / "proposer_allocations" / "alloc-9" / "manifest.json").exists()
    assert (tmp_path / "proposer_allocations" / "alloc-9" / "manifest.json").read_text().find('"kind": "proposer"') != -1


def test_supervisor_uses_its_own_worker_artifact(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch)

    result_path = submitter.submit_supervisor("decision-1", {
        "snapshot": {"epoch_id": "epoch-0", "watermark": "w"},
        "proposer_capacity": 2,
    })

    work_dir = tmp_path / "supervisor_decisions" / "decision-1"
    manifest = json.loads((work_dir / "manifest.json").read_text())
    assert result_path == str(work_dir / "result.json")
    assert manifest["kind"] == "supervisor"
    assert "-m supervisor.cli" in (work_dir / "job.sh").read_text()


def test_integrator_uses_request_scoped_worker_artifact(tmp_path, monkeypatch):
    submitter = HTCondorSubmitter(tmp_path, _config(), python="/usr/bin/python3")
    _fake_submit(monkeypatch)

    result_path = submitter.submit_integrator("request-1", {
        "request": {"integration_request_id": "request-1"},
    })

    work_dir = tmp_path / "integration_requests" / "request-1"
    assert result_path == str(work_dir / "result.json")
    assert json.loads((work_dir / "manifest.json").read_text())["kind"] == "integrator"
    assert "-m scientist.integrator_cli" in (work_dir / "job.sh").read_text()
