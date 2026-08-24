"""Ablation figure: performance vs budget (or vs time) across SimpleEvolution arms.

One run-dir is one seed of one arm; every run-dir is a standard SimpleEvolution
run-dir, so we reuse ``load_tree_view`` + ``budget_series`` for the
(cumulative_cost, best_so_far) projection and normalise the y-axis to the
run's own measured root baseline (× multiple). ``x_axis="time"`` projects the
same running best onto elapsed hours since the run's root node was created —
the axis that matters for time-capped comparisons. Per arm, seed curves are
stepped onto a shared x grid and aggregated as median ± min/max band.

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
# "tree" is the Supervisor-gated tree (sole admission gate, no frontier
# fallback); it inherits the aqua slot the old frontier topk tree wore.
ARM_COLORS = {
    "coding-agent": "#2a78d6",  # blue
    "loop": "#eb6834",          # orange
    "topk": "#1baf7a",          # aqua
    "tree": "#1baf7a",          # aqua (successor of topk's slot)
    "seat-v6": "#8250df",       # purple, categorical slot 4 (~6:1 on surface)
}
ARM_LABELS = {
    "coding-agent": "coding agent",
    "loop": "serial loop (k=1)",
    "topk": "top-k tree (k=3)",
    "tree": "supervisor tree",
    "seat-v6": "seat-v6 (supervisor buys seats)",
}
# Short label + scheduling shape for the title/footer, per arm.
ARM_SHORT = {
    "coding-agent": "coding agent",
    "loop": "serial loop",
    "topk": "top-k tree",
    "tree": "supervisor tree",
    "seat-v6": "seat-v6",
}
ARM_SHAPE = {
    "coding-agent": "slots=1·inflight=1",
    "loop": "slots=1·inflight=1",
    "topk": "k=3 frontier·slots=1",
    "tree": "supervisor gate·slots=4·inflight=4",
    "seat-v6": "seats=node×lens·inflight=4",
}

# Ink / chrome tokens.
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"


def _run_points(
    run_dir: Path, x_axis: str = "cost"
) -> tuple[list[tuple[float, float]], str, float] | None:
    """(x, ×multiple) series for one run-dir, or None if unusable.

    ``x_axis="cost"`` projects onto cumulative LLM spend (usage ledger replayed
    at the run's configured prices); ``x_axis="time"`` onto elapsed wall-clock
    hours since the root node's creation; ``x_axis="worktime"`` onto cumulative
    DRIVER-RUNNING hours (dead gaps between killed and relaunched drivers
    collapse to zero — the fair axis when arms were paused mid-run).  All
    apply the same acceptance rule as ``best_so_far``: only gate-passed nodes
    with a measured objective enter the running best.
    """
    try:
        view = load_tree_view(run_dir)
    except Exception as exc:  # malformed / incomplete run-dir
        print(f"  [plot] skip {run_dir}: {exc}", flush=True)
        return None
    root = view.root_objective.get(view.objective_key)
    if root in (None, 0) or not math.isfinite(root):
        print(f"  [plot] skip {run_dir}: no root baseline", flush=True)
        return None

    if x_axis == "cost":
        series = budget_series(view, run_dir).get("total", [])
        if not series:
            print(f"  [plot] skip {run_dir}: no completed experiments", flush=True)
            return None
    else:
        # Running best over gate-passed measured nodes in completion order —
        # the same envelope best_so_far builds on the experiment-ordinal axis.
        ordered = sorted(
            (v for v in view.nodes if v.experiment_idx is not None),
            key=lambda v: v.created_at,
        )
        created = [v.created_at for v in ordered]
        if len(created) < 2:
            print(f"  [plot] skip {run_dir}: no completed experiments", flush=True)
            return None
        if x_axis == "worktime":
            xs = _uptime_axis(run_dir, created)
        else:
            t0 = min(v.created_at for v in view.nodes)  # root creation
            xs = [(c - t0) / 3600.0 for c in created]
        best = None
        series = []
        for x, v in zip(xs, ordered, strict=True):
            if not v.passed or v.objective is None or not math.isfinite(v.objective):
                continue
            if best is None or (
                v.objective < best if view.lower_is_better else v.objective > best
            ):
                best = v.objective
            series.append((x, best))
        if not series:
            print(f"  [plot] skip {run_dir}: no completed experiments", flush=True)
            return None

    lower = view.lower_is_better
    points = [
        (
            float(x),
            (root / best) if lower else (best / root),
        )
        for x, best in series
        if math.isfinite(best) and best > 0
    ]
    return points, view.objective_key, float(root)


def _uptime_segments(run_dir: Path):
    """Wall-clock [start, end] of every driver generation of a run.

    The run log's own elapsed clock (reset to 0 at each relaunch) gives each
    generation's exact lifetime; the log file's mtime anchors the final
    generation's end (its last write); walking backwards, each earlier
    generation ended when its rows stopped — its last DB row before the next
    generation's start bounds the death (the kill-vs-drain tail between the
    last row and the actual death is unbinned, typically a few minutes).

    Returns None when the log or its clock is unusable — the caller then
    falls back to the wall axis.
    """
    import re
    import sqlite3

    log = run_dir.parents[1] / f"{run_dir.parent.name}.run.log"
    if not log.exists():
        return None
    elapsed = [
        float(m) * 3600.0
        for m in re.findall(r"elapsed=([0-9.]+)h", log.read_text(errors="replace"))
    ]
    if not elapsed:
        return None
    durations: list[float] = []
    prev = elapsed[0]
    cur_max = elapsed[0]
    for e in elapsed[1:]:
        if e + 1e-6 < prev:  # clock reset: new driver generation
            durations.append(cur_max)
            cur_max = e
        else:
            cur_max = max(cur_max, e)
        prev = e
    durations.append(cur_max)

    db = run_dir / "simpleevo.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    try:
        stamps: list[float] = []
        for table in ("scheduler_events", "nodes", "experiments",
                      "proposer_allocations", "attempts"):
            try:
                stamps += [r[0] for r in conn.execute(
                    f"SELECT created_at FROM {table}")]
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    stamps.sort()

    # Backward walk: the final generation ends at the log's last write
    # (mtime); each earlier generation ended when its rows stopped — the
    # last DB row before the next generation's start bounds its death.
    spans: list[tuple[float, float]] = []
    end = log.stat().st_mtime
    for k in range(len(durations) - 1, -1, -1):
        start = end - durations[k]
        spans.append((start, end))
        if k > 0:
            # 2-minute guard: the next generation's start is back-computed
            # from a 2-decimal elapsed, so its first rows can land seconds
            # "before" it — exclude them from this generation's death bound.
            prior_rows = [t for t in stamps if t < start - 120.0]
            end = prior_rows[-1] if prior_rows else start
    spans.reverse()
    return spans


def _uptime_axis(run_dir: Path, created: list[float]) -> list[float] | None:
    """Map node-creation wall times onto cumulative work hours.

    Dead gaps between driver generations collapse to zero width; inside a
    generation the offset from its start is preserved.  Falls back to
    wall-clock (since the first activity) when no generations can be
    recovered.
    """
    segments = _uptime_segments(run_dir)
    if segments is None:
        first = min(created)
        return [(c - first) / 3600.0 for c in created]
    acc: list[float] = []
    total = 0.0
    for start, end in segments:
        acc.append(total)
        total += end - start
    out = []
    for c in created:
        val = total
        for i, (start, end) in enumerate(segments):
            if c <= end:
                val = acc[i] + min(max(0.0, c - start), end - start)
                break
        out.append(val / 3600.0)
    return out


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
    x_axis: str = "cost",
    human_ref_lps: float = 0.0,
    unify_baseline: bool = False,
) -> str:
    """Render the performance overlay (vs cost or vs time) and return the path.

    ``human_ref_lps`` (optional) draws a muted dashed line at the absolute
    lps value.  With ``unify_baseline`` every curve is re-expressed over the
    average of the plotted runs' root baselines (one shared denominator, one
    expert line); without it each curve keeps its own baseline and the
    reference is drawn per arm.
    """
    if x_axis not in ("cost", "time", "worktime"):
        raise ValueError(
            f"unknown x_axis {x_axis!r}; expected 'cost', 'time' or 'worktime'")
    runs_root = Path(runs_root)
    arms = arms or list(ARM_LABELS)

    per_arm: dict[str, list[list[tuple[float, float]]]] = {}
    root_by_arm: dict[str, list[float]] = {}
    total_runs = 0
    for arm in arms:
        seed_dirs = sorted(
            (runs_root / arm).glob("seed-*"), key=lambda p: p.name
        ) if (runs_root / arm).exists() else []
        for run_dir in seed_dirs:
            result = _run_points(run_dir, x_axis)
            if result is None:
                continue
            points, _obj, root = result
            per_arm.setdefault(arm, []).append(points)
            root_by_arm.setdefault(arm, []).append(root)
            total_runs += 1

    if total_runs == 0:
        raise SystemExit(f"no usable runs found under {runs_root}")

    # One shared denominator: re-scale every run's ×-own-baseline curve onto
    # the average root baseline (y × own_root/avg = absolute lps / avg).
    unified_root = 0.0
    if unify_baseline:
        all_roots = [r for roots in root_by_arm.values() for r in roots]
        unified_root = sum(all_roots) / len(all_roots)
        for arm, roots in root_by_arm.items():
            for i, scale_root in enumerate(roots):
                per_arm[arm][i] = [
                    (x, y * scale_root / unified_root) for x, y in per_arm[arm][i]
                ]

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
        # Where this arm's own data actually ends.  ``_stepped`` would carry
        # best-so-far forward across the whole shared grid, but a flat tail
        # to the right edge reads as "worked the whole time, plateaued" —
        # for an arm that stopped early that's a false story.  Mask the
        # curve beyond the arm's true last data point: the line physically
        # ENDS where the work ended (early-stop markers below say why).
        last_x = max(p[-1][0] for p in runs)
        last_idx = max(
            i for i, x in enumerate(grid) if x <= last_x + 1e-9
        )
        median = median[: last_idx + 1] + [math.nan] * (len(grid) - last_idx - 1)
        lo = lo[: last_idx + 1] + [math.nan] * (len(grid) - last_idx - 1)
        hi = hi[: last_idx + 1] + [math.nan] * (len(grid) - last_idx - 1)
        ax.fill_between(grid, lo, hi, color=color, alpha=0.10, linewidth=0, zorder=1)
        ax.plot(
            grid, median, color=color, linewidth=2.0, solid_joinstyle="round",
            zorder=3,
        )
        arm_info[arm] = {
            "median": median, "lo": lo, "hi": hi, "color": color,
            "last_x": last_x,
        }

    # Early-stop markers: an arm whose last data point sits well before the
    # grid's right edge gets a dotted drop-line + note at its true end, so
    # the flat tail reads as "idle", not "working".
    _STOP_VERBS = {"coding-agent": "self-terminated"}
    for arm, info in arm_info.items():
        last_x = info["last_x"]
        if last_x > grid[-1] * 0.90:
            continue
        idx = min(range(len(grid)), key=lambda i: abs(grid[i] - last_x))
        y = info["median"][idx]
        if math.isnan(y):
            continue
        ax.plot(
            [last_x, last_x], [ax.get_ylim()[0], y],
            color=info["color"], linewidth=1.0, linestyle=(0, (2, 3)),
            alpha=0.55, zorder=2,
        )
        verb = _STOP_VERBS.get(arm, "stopped")
        ax.annotate(
            f"{verb} @{last_x:.1f}h\n(no data beyond)",
            xy=(last_x, y), xytext=(6, 14), textcoords="offset points",
            fontsize=8, color=INK_MUTED, ha="left",
            arrowprops={
                "arrowstyle": "-", "color": INK_MUTED,
                "lw": 0.8, "alpha": 0.6,
            },
        )

    # Baseline (unmodified source = 1×).
    ax.axhline(1.0, color=BASELINE, linewidth=1.0, linestyle="--", zorder=2)

    # External human-expert reference.  Unified: one line at ref/avg-root.
    # Otherwise per arm (ref over that arm's own measured root — the lines
    # differ because the denominators do).
    if human_ref_lps > 0:
        if unify_baseline:
            ref_mult = human_ref_lps / unified_root
            ax.axhline(
                ref_mult, color=INK_MUTED, linewidth=1.0,
                linestyle=(0, (4, 3)), alpha=0.9, zorder=2,
            )
            ax.annotate(
                f"upstream author-optimized kernel (-k 1) "
                f"≈{human_ref_lps/1e6:.2f}M lps "
                f"({ref_mult:.2f}× avg baseline)",
                xy=(grid[0], ref_mult), xytext=(4, 3),
                textcoords="offset points", fontsize=8,
                color=INK_MUTED, va="bottom",
            )
        else:
            labelled = False
            for arm, info in arm_info.items():
                roots = root_by_arm.get(arm, [])
                if not roots:
                    continue
                ref_mult = human_ref_lps / _median(sorted(roots))
                ax.axhline(
                    ref_mult, color=info["color"], linewidth=0.9,
                    linestyle=(0, (4, 3)), alpha=0.45, zorder=2,
                )
                if not labelled:
                    ax.annotate(
                        f"upstream author-optimized kernel (-k 1) "
                        f"≈{human_ref_lps/1e6:.2f}M lps "
                        "(per arm's own baseline)",
                        xy=(grid[0], ref_mult), xytext=(4, 3),
                        textcoords="offset points", fontsize=8,
                        color=INK_MUTED, va="bottom",
                    )
                    labelled = True

    # Direct end-labels: final median × value at the arm's own end (right
    # edge for full-length arms; true last point for early-stopped ones).
    # The value is the raw last point of each seed (the grid's last tick at
    # or before it would round away a final jump).
    for arm, info in arm_info.items():
        finals = [p[-1] for p in per_arm[arm]]
        final_x = max(x for x, _ in finals)
        final_y = _median([y for _, y in finals])
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
    ax.set_xlabel(
        {"time": "elapsed wall-clock (h)",
         "worktime": "driver work time (h, gaps excluded)",
         "cost": "cumulative LLM cost (USD)"}[x_axis],
        color=INK_PRIMARY,
    )
    ax.set_ylabel("lookups/s vs baseline (×)", color=INK_PRIMARY)
    plotted = " vs ".join(ARM_SHORT[arm] for arm in arm_info)
    ax.set_title(
        f"XSBench: performance vs "
        f"{'time' if x_axis == 'time' else 'budget'} — {plotted}",
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
        f"{total_runs} runs · "
        + " | ".join(f"{ARM_SHORT[arm]}: {ARM_SHAPE[arm]}" for arm in arm_info),
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
    parser.add_argument("--x-axis", default="cost", choices=("cost", "time"))
    parser.add_argument(
        "--human-ref-lps", type=float, default=0.0,
        help="absolute lps of a human-expert reference kernel; drawn per arm "
        "over that arm's own baseline",
    )
    parser.add_argument(
        "--unify-baseline", action="store_true",
        help="re-express every curve over the average of the plotted runs' "
        "root baselines (one shared denominator, one expert line)",
    )
    args = parser.parse_args()
    path = render_ablation(
        args.runs_root, out_path=args.out, arms=args.arms, log_y=args.log_y,
        x_axis=args.x_axis, human_ref_lps=args.human_ref_lps,
        unify_baseline=args.unify_baseline,
    )
    print(f"wrote {path}")
