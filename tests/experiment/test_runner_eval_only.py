"""Eval-only experiments: the run-start baseline path.

The baseline must reuse the experiment worker end-to-end — same worktree
provisioning, same eval commands, same gate application — with exactly one
difference: no executor (the pristine root SHA needs no implementation agent).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from experiment.contracts import EvaluationResult, ExperimentRequest
from experiment.runner import ExperimentRunner


def _write_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path, check=True, capture_output=True,
    )


def _request(tmp_path: Path, *, eval_commands: tuple[str, ...]) -> ExperimentRequest:
    from experiment.git_worktree import GitWorkspaceProvider

    repo = tmp_path / "repo"
    _write_repo(repo)
    # The runner provisions worktrees from the run-dir clone, exactly like a
    # real worker; initialize() creates it.
    run_dir = tmp_path / "run"
    provider = GitWorkspaceProvider(run_dir, repo)
    root_sha = provider.initialize()
    return ExperimentRequest(
        experiment_id="baseline",
        proposal_id="",
        parent_node_id="",
        parent_sha=root_sha,
        proposal="baseline evaluation",
        repo_path=repo,
        run_dir=run_dir,
        editable_paths=("README.md",),
        frozen_paths=(),
        eval_commands=eval_commands,
        metrics_schema={
            "objective": {"key": "TOTAL_MS", "lower_is_better": True},
            "gates": [{"key": "GATE_A"}],
        },
        runtime_image=Path("/tmp/nonexistent.sif"),
        eval_timeout_seconds=60,
        eval_only=True,
    )


@pytest.fixture()
def _stubbed_eval(monkeypatch):
    """Stub the Apptainer evaluator; expose per-test EvaluationResults."""
    from experiment import runner as runner_mod

    state = {"result": None}

    def _fake_run_evaluator(self, workspace):
        return state["result"]

    monkeypatch.setattr(
        runner_mod.ExperimentRunner, "_run_evaluator", _fake_run_evaluator)
    return state


def test_eval_only_skips_executor_and_commits_nothing(tmp_path, _stubbed_eval):
    """Baseline: eval runs, no executor, no new commit, gates applied."""
    _stubbed_eval["result"] = EvaluationResult(
        text="TOTAL_MS=100\nGATE_A=PASS",
        metrics={"TOTAL_MS": 100.0, "GATE_A": True},
        returncodes=(0,),
    )
    request = _request(tmp_path, eval_commands=("echo TOTAL_MS=100",))
    result = ExperimentRunner(request).run()

    assert result.status == "COMPLETED"
    assert result.metrics["TOTAL_MS"] == 100.0
    assert result.gate.passed is True
    assert result.sha == request.parent_sha  # no new commit — root SHA itself
    assert result.changed_paths == ()
    assert "baseline: eval-only" in result.execution.output


def test_eval_only_failing_objective_aborts_loudly(tmp_path, _stubbed_eval):
    """A non-finite/missing objective on the pristine tree must abort the
    worker (infra), never return a COMPLETED result with no anchor."""
    _stubbed_eval["result"] = EvaluationResult(
        text="TOTAL_MS=nan\nGATE_A=PASS",
        metrics={"GATE_A": True},
        returncodes=(0,),
    )
    request = _request(tmp_path, eval_commands=("echo broken",))
    with pytest.raises(RuntimeError, match="TOTAL_MS is missing or not finite"):
        ExperimentRunner(request).run()


def test_eval_only_failing_gate_aborts_loudly(tmp_path, _stubbed_eval):
    """A failing gate on the pristine tree must abort the worker loudly —
    the baseline is the acceptance test of the task itself."""
    _stubbed_eval["result"] = EvaluationResult(
        text="TOTAL_MS=100\nGATE_A=FAIL",
        metrics={"TOTAL_MS": 100.0, "GATE_A": False},
        returncodes=(0,),
    )
    request = _request(tmp_path, eval_commands=("echo GATE_A=FAIL",))
    with pytest.raises(RuntimeError, match="baseline gate"):
        ExperimentRunner(request).run()
