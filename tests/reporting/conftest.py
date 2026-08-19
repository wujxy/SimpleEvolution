"""Shared run-dir factory for reporting tests.

Builds a real run directory (``task.yaml`` + ``simpleevo.db``) through the same
``ResearchStore`` ingest path the scheduler uses, so the projections under test
read exactly what a live run would produce.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simpleevo.config import EvolutionConfig, save_config
from simpleevo.db.store import GateDecision, GateResult, Proposal, ResearchStore


def _gate(passed: bool) -> GateDecision:
    return GateDecision(
        results={"PASS": GateResult(passed, "")},
        passed=passed,
    )


def _config(base: Path, *, lower_is_better: bool) -> EvolutionConfig:
    # The axis also appears at the schema TOP level so the frontier policy's
    # per-axis direction lookup (``frontier._axis_direction``) sees it; the
    # ``objective`` block drives ``TreeView.lower_is_better``.
    return EvolutionConfig(
        goal="test goal",
        repo_path=base / "repo",
        runtime_image=base / "img.sif",
        editable_paths=("src",),
        frozen_paths=(),
        eval_commands=("echo ok",),
        metrics_schema={
            "objective": {"key": "ms_per_call", "lower_is_better": lower_is_better},
            "ms_per_call": {"lower_is_better": lower_is_better},
            "gates": [
                {"key": "CORRECTNESS", "description": ""},
                {"key": "DRIFT", "description": ""},
            ],
        },
        axes=("ms_per_call",),
    )


def _add_experiment(
    store, root, episode, *, eid, created_at, status,
    metrics=None, sha=None, passed=True,
) -> None:
    """Create one experiment row and, for terminal statuses, ingest its result."""
    with store.transaction() as tx:
        proposal = tx.create_proposal(Proposal(
            proposal_id=f"{eid}-prop",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            instruction="try",
            rationale={},
            status="queued",
            created_at=created_at - 0.5,
        ))
        tx.create_experiment(
            experiment_id=eid,
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="running",
            created_at=created_at,
        )
    if status == "running":
        return
    store.ingest_experiment_result(
        experiment_id=eid,
        result_sha=sha,
        metrics=metrics or {},
        gate_result=_gate(passed),
        status=status,
    )


def build_run(
    base: Path,
    *,
    lower_is_better: bool = True,
    root_metrics: dict | None = None,
    completed_values: tuple[float, float] = (80.0, 60.0),
    pending_extra: bool = False,
) -> Path:
    """One run dir with the standard evolution scenario.

    root(ms_per_call=100) -> exp-1 completed(80, child n1) -> exp-2 gate_rejected
    -> exp-3 no_change -> exp-4 completed(60, child n2); all experiments hang off
    the root so the rejection hole in the ordinal axis is visible.
    """
    if root_metrics is None:
        root_metrics = {"ms_per_call": 100.0}
    run_dir = base / "run"
    run_dir.mkdir(parents=True)
    save_config(
        run_dir / "task.yaml", _config(base, lower_is_better=lower_is_better),
    )

    store = ResearchStore(run_dir / "simpleevo.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="rootsha",
            metrics=dict(root_metrics),
            gate_result=_gate(True),
            depth=0,
            status="active",
            created_at=1.0,
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )

    _add_experiment(
        store, root, episode, eid="exp-1", created_at=10.0,
        status="completed", metrics={"ms_per_call": completed_values[0]},
        sha="sha1",
    )
    _add_experiment(
        store, root, episode, eid="exp-2", created_at=20.0,
        status="gate_rejected",
        metrics={"CORRECTNESS": False, "DRIFT": False}, sha="sha2", passed=False,
    )
    _add_experiment(
        store, root, episode, eid="exp-3", created_at=30.0,
        status="no_change", metrics={}, sha=None,
    )
    _add_experiment(
        store, root, episode, eid="exp-4", created_at=40.0,
        status="completed", metrics={"ms_per_call": completed_values[1]},
        sha="sha4",
    )
    if pending_extra:
        _add_experiment(
            store, root, episode, eid="exp-5", created_at=50.0, status="running",
        )
    return run_dir


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return build_run(tmp_path)
