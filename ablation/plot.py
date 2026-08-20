"""Ablation figure: performance vs budget across SimpleEvolution arms.

One run-dir is one seed of one arm; every run-dir is a standard SimpleEvolution
run-dir, so we reuse ``load_tree_view`` + ``budget_series`` for the
(cumulative_cost, best_so_far) projection and normalise the y-axis to the
run's own measured root baseline (× multiple). Per arm, seed curves are stepped
onto a shared cost grid and aggregated as median ± min/max band.

Palette: validated categorical slots 1-3 (blue / orange / aqua) from the
project dataviz palette, light surface. The aqua slot sits below 3:1 contrast
on the surface, so every line carries a direct end-label (relief channel).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D

from simpleevo.reporting.data import budget_series, load_tree_view

# Validated categorical palette (light surface), slots 1-3 in arm order.
ARM_COLORS = {
    "coding-agent": "#2a78d6",  # blue
    "loop": "#eb6834",          # orange
    "topk": "#1baf7a",          # aqua
}
ARM_LABELS = {
    "coding-agent": "coding agent",
    "loop": "serial loop (k=1)",
    "topk": "top-k tree (k=3)",
}

# Ink / chrome tokens.
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"


def _run_points(run_dir: Path) -> tuple[list[tuple[float, float]], str, float] | None:
    """(cost, ×multiple) series for one run-dir, or None if unusable."""
    try:
        view = load_tree_view(run_dir)
    except Exception as exc:  # malformed / incomplete run-dir
        print(f"  [plot] skip {run_dir}: {exc}", flush=True)
        return None
    series = budget_series(view, run_dir).get("total", [])
    root = view.root_objective.get(view.objective_key)
    if root in (None, 0) or not math.isfinite(root):
        print(f"  [plot] skip {run_dir}: no root baseline", flush=True)
        return None
    if not series:
        print(f"  [plot] skip {run_dir}: no completed experiments", flush=True)
        return None
    lower = view.lower_is_better
    points = [
        (
            float(cost),
            (root / best) if lower else (best / root),
        )
        for cost, best in series
        if math.isfinite(best) and best > 0
    ]
    return points, view.objective_key, float(root)


def _stepped(arr: list[tuple[float, float]], grid: list[float]) -> list[float]:
    """Last value whose cost <= x for each grid point (NaN before first)."""
    out: list[float] = []
    j = 0
    for x in grid:
        while j < len(arr) and arr[j][0] <= x:
            j += 1
        out.append(arr[j - 1][1] if j > 0 else math.nan)
    return out


def _grid_bounds(all_points: list[list[tuple[float, float]]], n: int = 60):
    if not all_points:
        return []
    hi = max(p[-1][0] for p in all_points)
    return [hi * i / max(1, n - 1) for i in range(n)]


def render_ablation(
    runs_root: str | Path,
    *,
    out_path: str | Path = "ablation.png",
    arms: list[str] | None = None,
    log_y: bool = False,
) -> str:
    """Render the budget-vs-performance overlay and return the output path."""
    runs_root = Path(runs_root)
    arms = arms or list(ARM_LABELS)

    per_arm: dict[str, list[list[tuple[float, float]]]] = {}
    total_runs = 0
    for arm in arms:
        seed_dirs = sorted(
            (runs_root / arm).glob("seed-*"), key=lambda p: p.name
        ) if (runs_root / arm).exists() else []
        for run_dir in seed_dirs:
            result = _run_points(run_dir)
            if result is None:
                continue
            points, _obj, _root = result
            per_arm.setdefault(arm, []).append(points)
            total_runs += 1

    if total_runs == 0:
        raise SystemExit(f"no usable runs found under {runs_root}")

    grid = _grid_bounds([p for arm_points in per_arm.values() for p in arm_points])
    if len(grid) < 2:
        raise SystemExit(f"not enough cost span to draw a curve under {runs_root}")

    fig, ax = plt.subplots(figsize=(9.5, 5.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    arm_info: dict[str, dict[str, Any]] = {}
    for arm in arms:
        runs = per_arm.get(arm)
        if not runs:
            continue
        stepped = [_stepped(p, grid) for p in runs]
        median = _column_median(stepped)
        lo = _column_min(stepped)
        hi = _column_max(stepped)
        color = ARM_COLORS[arm]
        ax.fill_between(grid, lo, hi, color=color, alpha=0.10, linewidth=0, zorder=1)
        ax.plot(
            grid, median, color=color, linewidth=2.0, solid_joinstyle="round",
            zorder=3,
        )
        arm_info[arm] = {"median": median, "lo": lo, "hi": hi, "color": color}

    # Baseline (unmodified source = 1×).
    ax.axhline(1.0, color=BASELINE, linewidth=1.0, linestyle="--", zorder=2)

    # Direct end-labels: final median × value at the right edge (relief for the
    # sub-3:1 aqua slot; text wears ink, identity stays on the mark).
    for arm, info in arm_info.items():
        final_x = grid[-1]
        final_y = info["median"][-1]
        if math.isnan(final_y):
            continue
        ax.plot([final_x], [final_y], "o", markersize=4, color=info["color"],
                markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=4)
        ax.annotate(
            f"{final_y:.2f}×",
            xy=(final_x, final_y),
            xytext=(final_x, final_y),
            textcoords="offset points",
            fontsize=9,
            color=INK_SECONDARY,
            va="center",
        )
        print(f"  [plot] {arm}: {len(per_arm[arm])} seed(s), final median {final_y:.2f}×", flush=True)

    # Recessive gridlines + ink.
    ax.grid(axis="y", color=GRIDLINE, linewidth=1.0, zorder=0)
    ax.tick_params(colors=INK_MUTED)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(BASELINE)
    ax.set_xlabel("cumulative LLM cost (USD)", color=INK_PRIMARY)
    ax.set_ylabel("lookups/s vs baseline (×)", color=INK_PRIMARY)
    ax.set_title(
        "XSBench: performance vs budget — coding agent vs serial loop vs top-k tree",
        color=INK_PRIMARY,
        fontsize=12,
    )
    if log_y:
        ax.set_yscale("log")
    ax.set_ylim(bottom=min(0.9, min(_min_any(info) for info in arm_info.values())))

    handles = [
        Line2D([], [], color=ARM_COLORS[arm], linewidth=2.0, label=ARM_LABELS[arm])
        for arm in arm_info
    ]
    legend = ax.legend(
        handles=handles,
        loc="upper left",
        frameon=False,
        labelcolor=INK_PRIMARY,
        fontsize=10,
    )
    fig.text(
        0.99, 0.01,
        f"{total_runs} runs · proposal_slots=1 · inflight=1 · generator_reseed=off",
        ha="right", va="bottom", color=INK_MUTED, fontsize=8,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return str(out_path)


def _column_median(rows: list[list[float]]) -> list[float]:
    n = len(rows[0])
    out: list[float] = []
    for i in range(n):
        vals = sorted(r[i] for r in rows if not math.isnan(r[i]))
        out.append(_median(vals) if vals else math.nan)
    return out


def _median(vals: list[float]) -> float:
    m = len(vals)
    return vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2


def _column_min(rows: list[list[float]]) -> list[float]:
    n = len(rows[0])
    return [
        min((r[i] for r in rows if not math.isnan(r[i])), default=math.nan)
        for i in range(n)
    ]


def _column_max(rows: list[list[float]]) -> list[float]:
    n = len(rows[0])
    return [
        max((r[i] for r in rows if not math.isnan(r[i])), default=math.nan)
        for i in range(n)
    ]


def _min_any(info: dict) -> float:
    return min(
        (v for v in (info["median"] + info["lo"]) if not math.isnan(v)),
        default=1.0,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="ablation-plot")
    parser.add_argument("--runs-root", default="runs/ablation", type=Path)
    parser.add_argument("--out", default="ablation.png", type=Path)
    parser.add_argument("--arms", nargs="*")
    parser.add_argument("--log-y", action="store_true")
    args = parser.parse_args()
    path = render_ablation(args.runs_root, out_path=args.out, arms=args.arms, log_y=args.log_y)
    print(f"wrote {path}")
