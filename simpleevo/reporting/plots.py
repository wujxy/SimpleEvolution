"""Matplotlib numeric plots (② scientific-analysis view).

``progress.png`` — the four-panel evolution-dynamics figure; ``progress_log.png``
is the same figure with the objective and × multiple panels on log y-axes:

- **(a) is the run improving?** — every gate-passed node as a scatter, the
  best-so-far envelope as a step, gate-rejected / no-change experiments as × at
  their parent's objective, and a dashed baseline reference line when the root
  was measured.
- **(b) how much better than baseline?** — two complementary views of per-axis
  best-so-far vs the measured root baseline, side by side: **%** change (falls
  back to absolute values when the root was never measured, as in legacy runs)
  and **× multiple** (readable when a % gain saturates, e.g. 100-1000×
  speedups; omitted without a baseline). Monotonic on purpose: a top-k frontier
  keeps dominated candidates resident, and plotting them as a "winner" step
  would misread as regression.
- **(c) where does the improvement come from?** — performance vs tree depth
  (width vs depth).

``budget.png`` — objective vs cumulative USD spend (proposer / executor / total).
``pareto.png`` — only when ≥2 meaningful axes exist, the trade-off front.

All series are read-only projections; nothing here mutates the run.
"""
from __future__ import annotations

from pathlib import Path

from .data import (
    TreeView, best_so_far, budget_series, experiment_marks,
    improvement_multiple_series, improvement_series, load_tree_view,
)


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _prepare():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    return plt


def _best_so_far_axis(
    view: TreeView, plt, ax, *, log_scale: bool = False,
) -> None:
    """Panel (a): passed-node cloud + best-so-far envelope + rejection ×."""
    if log_scale:
        ax.set_yscale("log")
    raw = [
        (v.experiment_idx, v.objective)
        for v in view.nodes
        if v.experiment_idx is not None and v.passed and v.objective is not None
    ]
    marks = [
        (idx, y, status)
        for idx, y, status in experiment_marks(view)
        if y is not None  # no fabricated y for unmeasured parents (e.g. root)
    ]
    points = best_so_far(view)
    if not raw and not marks:
        ax.text(
            0.5, 0.5, "no completed experiments yet",
            ha="center", va="center", transform=ax.transAxes,
        )
        ax.set_xlabel("experiment #")
        ax.set_ylabel(view.objective_key)
        return
    if raw:
        ax.scatter(
            [p[0] for p in raw], [p[1] for p in raw],
            s=14, alpha=0.35, color="gray", label="passed nodes",
        )
    if points:
        ax.step(
            [p[0] for p in points], [p[1] for p in points],
            where="post", color="tab:blue", label="best-so-far",
        )
    if marks:
        ax.scatter(
            [p[0] for p in marks], [p[1] for p in marks],
            s=40, marker="x", color="tab:red", linewidths=1.5,
            label="gate-rejected / no-change",
        )
    direction = "lower" if view.lower_is_better else "higher"
    baseline_value = _num(view.root_objective.get(view.objective_key))
    if baseline_value is not None:
        ax.axhline(
            baseline_value, color="tab:gray", linestyle="--", linewidth=1.0,
            label="baseline",
        )
    ax.set_xlabel("experiment #")
    ax.set_ylabel(view.objective_key)
    ax.set_title(
        f"Q: is the run improving? — {view.objective_key} vs experiment "
        f"({direction} is better)" + (" [log]" if log_scale else "")
    )
    ax.grid(alpha=0.3)
    ax.legend()


def _improvement_axis(view: TreeView, plt, ax) -> None:
    """Panel (b): best-so-far as % vs the measured baseline.

    Question: how much better than the unmodified source? Monotonic so a
    dominated top-k frontier resident can never read as a regression.
    """
    series = improvement_series(view)
    relative = any(
        view.root_objective.get(ax) not in (None, 0) for ax in view.axes
    )
    colors = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple"]
    drawn = False
    for i, axis_name in enumerate(view.axes):
        points = series.get(axis_name, [])
        if not points:
            continue
        ax.step(
            [p[0] for p in points], [p[1] for p in points],
            where="post", color=colors[i % len(colors)],
            label=axis_name,
        )
        drawn = True
    if not drawn:
        ax.text(
            0.5, 0.5, "no measured axes yet",
            ha="center", va="center", transform=ax.transAxes,
        )
    if relative:
        ax.axhline(
            0, color="tab:gray", linestyle="--", linewidth=1.0,
            label="baseline (0%)",
        )
    ax.set_xlabel("experiment #")
    ax.set_ylabel("% vs baseline" if relative else "value")
    ax.set_title(
        "Q: how much better than baseline? (% vs root)"
        if relative
        else "Q: how much better than baseline? (root baseline missing → absolute)"
    )
    ax.grid(alpha=0.3)
    if drawn:
        ax.legend()


def _improvement_multiple_axis(
    view: TreeView, plt, ax, *, log_scale: bool = False,
) -> None:
    """Panel (b2): best-so-far as × multiple vs the measured baseline.

    The same "how much better than baseline?" question in × units, so huge
    gains (e.g. tiny_algo's 100-1000× speedups) stay readable next to the %
    view instead of saturating at ~100%.  ``log_scale`` keeps an early small
    gain and a later huge one both legible on one axis (720× and 7980×).
    """
    if log_scale:
        ax.set_yscale("log")
    series = improvement_multiple_series(view)
    colors = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple"]
    drawn = False
    for i, axis_name in enumerate(view.axes):
        points = series.get(axis_name, [])
        if not points:
            continue
        ax.step(
            [p[0] for p in points], [p[1] for p in points],
            where="post", color=colors[i % len(colors)],
            label=axis_name,
        )
        drawn = True
    if not drawn:
        ax.text(
            0.5, 0.5, "no baseline measured yet",
            ha="center", va="center", transform=ax.transAxes,
        )
    if drawn:
        ax.axhline(
            1, color="tab:gray", linestyle="--", linewidth=1.0,
            label="baseline (1×)",
        )
    ax.set_xlabel("experiment #")
    ax.set_ylabel("× vs baseline")
    ax.set_title(
        "Q: how much better than baseline? (× multiple)"
        + (" [log]" if log_scale else "")
    )
    ax.grid(alpha=0.3)
    if drawn:
        ax.legend()


def _depth_axis(
    view: TreeView, plt, ax, *, log_scale: bool = False,
) -> None:
    """Panel (c): where improvement comes from — performance vs tree depth."""
    if log_scale:
        ax.set_yscale("log")
    passed = [
        (v.depth, v.objective)
        for v in view.nodes
        if v.passed and v.objective is not None
    ]
    if not passed:
        ax.text(
            0.5, 0.5, "no passed nodes with objective yet",
            ha="center", va="center", transform=ax.transAxes,
        )
        ax.set_xlabel("tree depth")
        ax.set_ylabel(view.objective_key)
        return
    ax.scatter(
        [p[0] for p in passed], [p[1] for p in passed],
        s=14, alpha=0.35, color="gray", label="passed nodes",
    )
    frontier = [
        (v.depth, v.objective)
        for v in view.nodes
        if v.passed and v.objective is not None and v.frontier_axes
    ]
    if frontier:
        ax.scatter(
            [p[0] for p in frontier], [p[1] for p in frontier],
            s=60, marker="*", color="tab:blue", label="frontier winner",
        )
    ax.set_xlabel("tree depth")
    ax.set_ylabel(view.objective_key)
    ax.set_title(
        "Q: where does the improvement live? (value vs depth)"
        + (" [log]" if log_scale else "")
    )
    ax.grid(alpha=0.3)
    ax.legend()


def _pareto_axis(view: TreeView, plt, ax):
    if len(view.axes) < 2:
        return None
    a0, a1 = view.axes[0], view.axes[1]
    pts = []
    for n in view.raw_nodes:
        if not n.gate_result.passed:
            continue
        v0 = _num(n.metrics.get(a0))
        v1 = _num(n.metrics.get(a1))
        if v0 is None or v1 is None:
            continue
        frontier = bool(view.by_id[n.node_id].frontier_axes)
        pts.append((v0, v1, frontier))
    if not pts:
        return None

    ax.scatter(
        [p[0] for p in pts if not p[2]], [p[1] for p in pts if not p[2]],
        s=14, alpha=0.4, color="gray", label="off-frontier",
    )
    ax.scatter(
        [p[0] for p in pts if p[2]], [p[1] for p in pts if p[2]],
        s=60, color="tab:blue", marker="*", label="frontier",
    )
    ax.set_xlabel(a0)
    ax.set_ylabel(a1)
    ax.set_title("Pareto scatter (first two axes)")
    ax.grid(alpha=0.3)
    ax.legend()
    return ax


def _budget_axis(view: TreeView, plt, ax, run_dir) -> None:
    series = budget_series(view, run_dir)
    styles = {
        "total": ("tab:blue", "-"),
        "proposer": ("tab:orange", "--"),
        "executor": ("tab:green", "--"),
    }
    drawn = False
    for name, (color, linestyle) in styles.items():
        points = series.get(name, [])
        if not points:
            continue
        ax.step(
            [p[0] for p in points], [p[1] for p in points],
            where="post", color=color, linestyle=linestyle,
            label=f"{name} spend",
        )
        drawn = True
    if not drawn:
        ax.text(
            0.5, 0.5, "no usage recorded yet",
            ha="center", va="center", transform=ax.transAxes,
        )
    ax.set_xlabel("cumulative cost (USD)")
    ax.set_ylabel(view.objective_key)
    ax.set_title(f"performance vs budget ({view.objective_key})")
    ax.grid(alpha=0.3)
    ax.legend()


def _draw_progress_figure(view: TreeView, plt, *, log_scale: bool):
    """Assemble the four progress panels on one canvas.

    ``log_scale`` applies a log y-axis to the objective / × multiple panels
    (the % panel stays linear — log is meaningless on a bounded percentage).
    """
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(8, 13))
    _best_so_far_axis(view, plt, ax1, log_scale=log_scale)
    _improvement_axis(view, plt, ax2)
    _improvement_multiple_axis(view, plt, ax3, log_scale=log_scale)
    _depth_axis(view, plt, ax4, log_scale=log_scale)
    fig.tight_layout()
    return fig


def write_plots(
    view: TreeView, out_dir: str | Path, run_dir: str | Path,
) -> list[Path]:
    """Write progress.png, progress_log.png, budget.png, and (≥2 axes) pareto.png."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plt = _prepare()

    fig = _draw_progress_figure(view, plt, log_scale=False)
    progress = out_dir / "progress.png"
    fig.savefig(progress, dpi=120)
    plt.close(fig)
    written = [progress]

    # Log-scale companion: the same questions, with the objective and the ×
    # multiple on log axes so gains across orders of magnitude stay legible
    # (e.g. tiny_algo's 719× -> 7980×).
    fig = _draw_progress_figure(view, plt, log_scale=True)
    progress_log = out_dir / "progress_log.png"
    fig.savefig(progress_log, dpi=120)
    plt.close(fig)
    written.append(progress_log)

    fig, ax = plt.subplots(figsize=(8, 4))
    _budget_axis(view, plt, ax, run_dir)
    fig.tight_layout()
    budget = out_dir / "budget.png"
    fig.savefig(budget, dpi=120)
    plt.close(fig)
    written.append(budget)

    if len(view.axes) >= 2:
        fig, ax = plt.subplots(figsize=(6, 5))
        if _pareto_axis(view, plt, ax) is not None:
            fig.tight_layout()
            pareto = out_dir / "pareto.png"
            fig.savefig(pareto, dpi=120)
            written.append(pareto)
        plt.close(fig)
    return written


def render(run_dir: str | Path, out_dir: str | Path) -> list[Path]:
    return write_plots(load_tree_view(run_dir), out_dir, run_dir)
