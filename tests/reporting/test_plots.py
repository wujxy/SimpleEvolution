"""Smoke tests: the 3-panel figure and budget.png render from a real run dir."""
from __future__ import annotations

from simpleevo.reporting.data import load_tree_view
from simpleevo.reporting.plots import render, write_plots


def test_write_plots_emits_progress_and_budget(run_dir, tmp_path):
    out = tmp_path / "out"
    written = write_plots(load_tree_view(run_dir), out, run_dir)
    paths = {p.name: p for p in written}
    assert "progress.png" in paths
    assert "progress_log.png" in paths  # log-scale companion figure
    assert "budget.png" in paths
    assert "pareto.png" not in paths  # single-axis task
    for p in written:
        assert p.stat().st_size > 1000


def test_render_returns_paths(run_dir, tmp_path):
    out = tmp_path / "out"
    paths = render(str(run_dir), str(out))
    assert any(p.name == "progress.png" for p in paths)
    assert (out / "progress.png").exists()
