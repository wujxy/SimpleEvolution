"""Matplotlib numeric plots (② scientific-analysis view).

Three views, the scientific core that SimpleLoop lacks:

- **best-so-far** objective vs completed experiments — is the run still finding
  better solutions as it spends experiment budget?
- **per-axis winner evolution** — how the frontier winner per axis moves over
  the run (replayed from nodes, since ``frontier_axes`` is only a snapshot).
- **Pareto scatter** — only when ≥2 meaningful axes exist, the trade-off front.

All series are read-only projections; nothing here mutates the run.
"""
from __future__ import annotations

from pathlib import Path

from .data import TreeView, best_so_far, load_tree_view, winner_history


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


def _best_so_far_axis(view: TreeView, plt, ax) -> None:
    points = best_so_far(view)
    if not points:
        ax.text(
            0.5, 0.5, "no completed experiments yet",
            ha="center", va="center", transform=ax.transAxes,
        )
        ax.set_xlabel("completed experiments")
        ax.set_ylabel(view.objective_key)
        return
    ax.step(
        [p[0] for p in points], [p[1] for p in points],
        where="post", color="tab:blue", label="best-so-far",
    )
    raw = [
        (v.experiment_idx, v.objective)
        for v in view.nodes
        if v.experiment_idx is not None and v.passed and v.objective is not None
    ]
    if raw:
        ax.scatter(
            [p[0] for p in raw], [p[1] for p in raw],
            s=14, alpha=0.35, color="gray", label="per-experiment",
        )
    direction = "lower" if view.lower_is_better else "higher"
    ax.set_xlabel("completed experiments")
    ax.set_ylabel(view.objective_key)
    ax.set_title(f"best-so-far {view.objective_key} ({direction} is better)")
    ax.grid(alpha=0.3)
    ax.legend()


def _winner_axis(view: TreeView, plt, ax) -> None:
    history = winner_history(view)
    colors = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple"]
    drawn = False
    for i, axis_name in enumerate(view.axes):
        series = history.get(axis_name, [])
        if not series:
            continue
        ax.step(
            [s[0] for s in series], [s[2] for s in series],
            where="post", color=colors[i % len(colors)],
            label=f"{axis_name} winner",
        )
        drawn = True
    if not drawn:
        ax.text(
            0.5, 0.5, "no measured axes yet",
            ha="center", va="center", transform=ax.transAxes,
        )
    ax.set_xlabel("completed experiments")
    ax.set_ylabel("value")
    ax.set_title("per-axis frontier winner evolution")
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


def write_plots(view: TreeView, out_dir: str | Path) -> list[Path]:
    """Write progress.png (best-so-far + winner) and pareto.png (≥2 axes)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plt = _prepare()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7))
    _best_so_far_axis(view, plt, ax1)
    _winner_axis(view, plt, ax2)
    fig.tight_layout()
    progress = out_dir / "progress.png"
    fig.savefig(progress, dpi=120)
    plt.close(fig)

    written = [progress]
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
    return write_plots(load_tree_view(run_dir), out_dir)
