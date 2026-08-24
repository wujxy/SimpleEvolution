"""Tests for ``simpleevo.cli``."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from simpleevo.cli import _ensure_baseline_measured, main
from simpleevo.config import EvolutionConfig
from simpleevo.db.queries import ResearchQueries
from simpleevo.db.store import GateDecision, ResearchStore


def _write_repo(path: Path) -> None:
    """Create a tiny git repo with one commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def _config_for_repo(repo: Path) -> EvolutionConfig:
    return EvolutionConfig(
        goal="test goal",
        repo_path=repo,
        runtime_image=Path("/tmp/nonexistent.sif"),
        editable_paths=("README.md",),
        frozen_paths=(),
        eval_commands=("echo TOTAL_MS=100",),
        metrics_schema={"objective": {"key": "TOTAL_MS", "lower_is_better": True}},
        axes=("TOTAL_MS",),
    )


def test_run_seeds_root_node_and_episode(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        repo = Path(tmp) / "repo"
        _write_repo(repo)
        config = _config_for_repo(repo)
        # The run-start baseline eval needs a real Apptainer runtime; stub it
        # out so the CLI wiring is what's under test (see _measure_baseline).
        monkeypatch.setattr(
            "simpleevo.cli._measure_baseline",
            lambda _cfg, _run_dir, _root_sha, _submitter: {"TOTAL_MS": 100.0},
        )
        config_path = Path(tmp) / "task.yaml"
        config_path.write_text(
            """
goal: test goal
repo_path: {repo}
runtime_image: /tmp/nonexistent.sif
editable_paths:
  - README.md
eval_commands:
  - echo TOTAL_MS=100
metrics_schema:
  objective:
    key: TOTAL_MS
    lower_is_better: true
axes:
  - TOTAL_MS
""".format(repo=repo),
            encoding="utf-8",
        )
        rc = main(["--run-dir", str(run_dir), "run", "--config", str(config_path), "--max-steps", "1"])
        assert rc == 0
        queries = ResearchQueries(run_dir / "simpleevo.db")
        nodes = queries.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].depth == 0
        # The run-start baseline populated the root's metrics (see stub above).
        assert nodes[0].metrics == {"TOTAL_MS": 100.0}
        episodes = queries.episodes_for_node(nodes[0].node_id, limit=1000)
        assert len(episodes) == 1
        assert (run_dir / "task.yaml").exists()


def test_init_seeds_root_node_and_saves_config():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        repo = Path(tmp) / "repo"
        _write_repo(repo)
        config_path = Path(tmp) / "task.yaml"
        config_path.write_text(
            """
goal: test goal
repo_path: {repo}
runtime_image: /tmp/nonexistent.sif
editable_paths:
  - README.md
eval_commands:
  - echo TOTAL_MS=100
metrics_schema:
  objective:
    key: TOTAL_MS
    lower_is_better: true
axes:
  - TOTAL_MS
""".format(repo=repo),
            encoding="utf-8",
        )
        rc = main(["--run-dir", str(run_dir), "init", "--config", str(config_path)])
        assert rc == 0
        queries = ResearchQueries(run_dir / "simpleevo.db")
        assert len(queries.list_nodes()) == 1
        assert (run_dir / "task.yaml").exists()


def test_resume_without_init_fails():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        rc = main(["--run-dir", str(run_dir), "resume"])
        assert rc == 1


def test_resume_continues_without_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        repo = Path(tmp) / "repo"
        _write_repo(repo)
        monkeypatch.setattr(
            "simpleevo.cli._measure_baseline",
            lambda _cfg, _run_dir, _root_sha, _submitter: {"TOTAL_MS": 100.0},
        )
        config_path = Path(tmp) / "task.yaml"
        config_path.write_text(
            """
goal: test goal
repo_path: {repo}
runtime_image: /tmp/nonexistent.sif
editable_paths:
  - README.md
eval_commands:
  - echo TOTAL_MS=100
metrics_schema:
  objective:
    key: TOTAL_MS
    lower_is_better: true
axes:
  - TOTAL_MS
""".format(repo=repo),
            encoding="utf-8",
        )
        main(["--run-dir", str(run_dir), "init", "--config", str(config_path)])
        # resume without --config, zero scheduler steps (no worker launch).
        rc = main(["--run-dir", str(run_dir), "resume", "--max-steps", "0"])
        assert rc == 0


def test_reseed_creates_fresh_episode(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        repo = Path(tmp) / "repo"
        _write_repo(repo)
        config = _config_for_repo(repo)
        monkeypatch.setattr(
            "simpleevo.cli._measure_baseline",
            lambda _cfg, _run_dir, _root_sha, _submitter: {"TOTAL_MS": 100.0},
        )
        config_path = Path(tmp) / "task.yaml"
        config_path.write_text(
            """
goal: test goal
repo_path: {repo}
runtime_image: /tmp/nonexistent.sif
editable_paths:
  - README.md
eval_commands:
  - echo TOTAL_MS=100
metrics_schema:
  objective:
    key: TOTAL_MS
    lower_is_better: true
axes:
  - TOTAL_MS
""".format(repo=repo),
            encoding="utf-8",
        )
        main(["--run-dir", str(run_dir), "run", "--config", str(config_path), "--max-steps", "1"])
        queries = ResearchQueries(run_dir / "simpleevo.db")
        node = queries.list_nodes()[0]
        before = len(queries.episodes_for_node(node.node_id, limit=100))
        rc = main(["--run-dir", str(run_dir), "reseed", "--node", node.node_id])
        assert rc == 0
        after = len(queries.episodes_for_node(node.node_id, limit=100))
        assert after == before + 1


def test_ensure_baseline_skips_when_root_has_metrics(tmp_path, monkeypatch):
    # A root that already carries metrics (previous run / measured baseline)
    # must not re-trigger the Apptainer baseline eval on resume.
    run_dir = tmp_path / "run"
    repo = tmp_path / "repo"
    _write_repo(repo)
    config = _config_for_repo(repo)
    run_dir.mkdir(parents=True)
    store = ResearchStore(run_dir / "simpleevo.db")
    with store.transaction() as tx:
        tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="rootsha",
            metrics={"TOTAL_MS": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
    monkeypatch.setattr(
        "simpleevo.cli._measure_baseline",
        lambda *args: pytest.fail("baseline eval must not run"),
    )
    _ensure_baseline_measured(config, run_dir, store)
    assert ResearchQueries(run_dir / "simpleevo.db").root_node().metrics == {
        "TOTAL_MS": 100.0,
    }


class _RecordingSubmitter:
    """Stand-in for a BaseSubmitter that records submit_baseline calls and
    writes a well-formed result envelope when asked to."""

    backend = "recording"
    presumes_dead_on_startup = True

    def __init__(self, metrics=None, outcome="COMPLETED"):
        self.calls = []
        self.metrics = {"TOTAL_MS": 100.0} if metrics is None else metrics
        self.outcome = outcome

    def submit_baseline(self, run_id, payload):
        self.calls.append((run_id, dict(payload)))
        result_dir = Path(self.result_root) / "experiments" / run_id
        result_dir.mkdir(parents=True, exist_ok=True)
        from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result
        write_result(
            result_dir / "result.json",
            WorkerResult(
                kind="experiment",
                request_id=run_id,
                status=WorkerStatus.COMPLETED,
                result={
                    "experiment_id": run_id,
                    "outcome": self.outcome,
                    "metrics": dict(self.metrics),
                    "eval_block": "TOTAL_MS=100.0\nEVAL_RESULT=ok\n",
                },
                usage=(),
                error=None,
                execution={},
            ),
        )
        return str(result_dir / "result.json")

    result_root = ""

    def remove_job(self, work_id, kind):
        pass


def test_measure_baseline_submits_eval_only_job(tmp_path, monkeypatch):
    """The baseline must go through the SAME backend as candidate experiments
    (SimpleLoop's hepjob contract): eval_only payload, result read from the
    submitted path, metrics returned."""
    from simpleevo.cli import _measure_baseline

    repo = tmp_path / "repo"
    _write_repo(repo)
    config = _config_for_repo(repo)
    submitter = _RecordingSubmitter()
    submitter.result_root = str(tmp_path / "run")

    metrics = _measure_baseline(config, tmp_path / "run", "abc123", submitter)

    assert metrics == {"TOTAL_MS": 100.0}
    run_id, payload = submitter.calls[0]
    assert run_id == "baseline"
    assert payload["eval_only"] is True
    assert payload["parent_sha"] == "abc123"


def test_measure_baseline_stale_result_is_cleared(tmp_path):
    """A leftover result.json from an earlier baseline attempt must not be
    read as the new job's result."""
    from simpleevo.cli import _measure_baseline
    from simpleevo.jobs.envelope import WorkerResult, WorkerStatus, write_result

    repo = tmp_path / "repo"
    _write_repo(repo)
    config = _config_for_repo(repo)
    run_dir = tmp_path / "run"
    stale = run_dir / "experiments" / "baseline" / "result.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    write_result(
        stale,
        WorkerResult(
            kind="experiment",
            request_id="baseline",
            status=WorkerStatus.COMPLETED,
            result={"outcome": "COMPLETED", "metrics": {"TOTAL_MS": 1.0}},
            usage=(),
            error=None,
            execution={},
        ),
    )
    submitter = _RecordingSubmitter(metrics={"TOTAL_MS": 250.0})
    submitter.result_root = str(run_dir)

    metrics = _measure_baseline(config, run_dir, "abc123", submitter)

    # The stale 1.0 must be gone; the fresh submitter's 250.0 wins.
    assert metrics == {"TOTAL_MS": 250.0}


def test_measure_baseline_rejects_failed_worker(tmp_path):
    """A worker-level failure (infra) must abort the run loudly."""
    from simpleevo.cli import _measure_baseline

    repo = tmp_path / "repo"
    _write_repo(repo)
    config = _config_for_repo(repo)
    submitter = _RecordingSubmitter(outcome="infra_failed")
    submitter.result_root = str(tmp_path / "run")

    with pytest.raises(RuntimeError, match="infra_failed"):
        _measure_baseline(config, tmp_path / "run", "abc123", submitter)


def test_measure_baseline_rejects_bad_objective(tmp_path):
    """A completed envelope with a missing objective must abort, not optimize
    blind against a nonexistent anchor."""
    from simpleevo.cli import _measure_baseline

    repo = tmp_path / "repo"
    _write_repo(repo)
    config = _config_for_repo(repo)
    submitter = _RecordingSubmitter(metrics={})  # objective missing
    submitter.result_root = str(tmp_path / "run")

    with pytest.raises(RuntimeError, match="missing or not"):
        _measure_baseline(config, tmp_path / "run", "abc123", submitter)
