"""Data-level tests for the reporting projections (unified ordinal x-axis)."""
from __future__ import annotations

from simpleevo.reporting.data import (
    best_so_far,
    experiment_marks,
    improvement_series,
    load_tree_view,
)

from .conftest import build_run


def test_experiment_ordinals_contiguous_over_all_experiments(run_dir):
    view = load_tree_view(run_dir)
    assert [e.exp_idx for e in view.experiments] == [1, 2, 3, 4]
    assert [e.status for e in view.experiments] == [
        "completed", "gate_rejected", "no_change", "completed",
    ]


def test_node_experiment_idx_matches_creating_experiment(run_dir):
    view = load_tree_view(run_dir)
    root = next(v for v in view.nodes if v.parent_node_id is None)
    assert root.experiment_idx is None
    # depth-1 nodes are children of root; their ordinals span the rejection hole
    children = [v for v in view.nodes if v.parent_node_id == root.node_id]
    assert sorted(v.experiment_idx for v in children) == [1, 4]


def test_experiment_marks(run_dir):
    view = load_tree_view(run_dir)
    assert experiment_marks(view) == [
        (2, 100.0, "gate_rejected"),
        (3, 100.0, "no_change"),
    ]


def test_best_so_far_uses_unified_ordinals(run_dir):
    view = load_tree_view(run_dir)
    assert best_so_far(view) == [(1, 80.0), (4, 60.0)]


def test_improvement_sign_lower_is_better(run_dir):
    view = load_tree_view(run_dir)
    assert view.root_objective == {"ms_per_call": 100.0}
    assert improvement_series(view)["ms_per_call"] == [(1, 20.0), (4, 40.0)]


def test_improvement_sign_higher_is_better(tmp_path):
    run_dir = build_run(
        tmp_path,
        lower_is_better=False,
        root_metrics={"ms_per_call": 10.0},
        completed_values=(12.0, 15.0),
    )
    view = load_tree_view(run_dir)
    assert improvement_series(view)["ms_per_call"] == [(1, 20.0), (4, 50.0)]


def test_improvement_fallback_when_root_missing(tmp_path):
    # Real runs seed the root with empty metrics; % must degrade to absolute.
    run_dir = build_run(tmp_path, root_metrics={})
    view = load_tree_view(run_dir)
    assert view.root_objective == {"ms_per_call": None}
    assert improvement_series(view)["ms_per_call"] == [(1, 80.0), (4, 60.0)]


def test_pending_experiment_consumes_ordinal_no_mark(tmp_path):
    run_dir = build_run(tmp_path, pending_extra=True)
    view = load_tree_view(run_dir)
    assert [e.exp_idx for e in view.experiments] == [1, 2, 3, 4, 5]
    # Non-terminal experiment: consumes an ordinal slot, but is never a mark
    assert view.experiments[-1].status == "running"
    assert view.experiments[-1].status not in ("gate_rejected", "no_change")
    assert experiment_marks(view) == [
        (2, 100.0, "gate_rejected"),
        (3, 100.0, "no_change"),
    ]
    assert best_so_far(view) == [(1, 80.0), (4, 60.0)]
