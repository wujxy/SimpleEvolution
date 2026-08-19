"""Graphviz tree renderer (③ paper-quality static tree).

One figure that tells the whole story at a glance:

- **node fill** = objective value (continuous colormap, so "better" is darker)
- **node border** = frontier membership (thick border = current frontier winner)
- **node shape** = valid (ellipse) vs dead (box) vs dormant (diamond)
- **edge** = experiment transition (parent -> child)

This makes visible the GEPA-specific story SimpleLoop cannot show: a branch
kept on the frontier while *not* globally best, that later evolves into the
best node.
"""
from __future__ import annotations

from pathlib import Path

from .data import TreeView, load_tree_view


def _short(node_id: str, n: int = 8) -> str:
    return node_id[:n]


def _label(view: TreeView, v) -> str:
    objective = (
        f"{view.objective_key}={v.objective:.4g}"
        if v.objective is not None else ""
    )
    return f"{_short(v.node_id)}\n{objective}".strip()


def _objective_fill(view: TreeView) -> dict[str, str]:
    """Map node id -> fill colour; better objective is darker."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import colormaps

    values = [
        v.objective for v in view.nodes
        if v.passed and v.objective is not None
    ]
    cmap = colormaps["viridis"]
    if not values:
        return {v.node_id: "#eeeeee" for v in view.nodes}
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1.0
    fills: dict[str, str] = {}
    for v in view.nodes:
        if v.objective is None or not v.passed:
            fills[v.node_id] = "#eeeeee"
            continue
        t = (v.objective - vmin) / span
        if not view.lower_is_better:
            t = 1.0 - t  # higher-is-better: best (max) -> dark
        r, g, b, _ = cmap(t)
        fills[v.node_id] = "#{:02x}{:02x}{:02x}".format(
            int(r * 255), int(g * 255), int(b * 255)
        )
    return fills


def _node_attrs(view: TreeView, v, fills: dict[str, str]) -> dict[str, str]:
    if v.status == "dead":
        attrs = {
            "shape": "box", "style": "filled,dashed",
            "fillcolor": "#eeeeee", "color": "#cc0000",
            "fontcolor": "#888888",
        }
    elif v.status == "dormant":
        attrs = {
            "shape": "diamond", "style": "filled",
            "fillcolor": fills[v.node_id], "color": "#999999",
        }
    else:
        attrs = {
            "shape": "ellipse", "style": "filled",
            "fillcolor": fills[v.node_id], "color": "#666666",
        }
    if v.frontier_axes:
        attrs["penwidth"] = "2.5"
        attrs["color"] = "#111111"
    return attrs


def render_dot(view: TreeView) -> str:
    """Return the .dot source for the tree (without rendering)."""
    from graphviz import Digraph

    fills = _objective_fill(view)
    dot = Digraph("research_tree", comment="SimpleEvolution research tree")
    dot.attr("node", fontname="Helvetica", fontsize="10")
    dot.attr("edge", color="#bbbbbb")
    dot.attr("graph", rankdir="TB", nodesep="0.35", ranksep="0.55")

    for v in view.nodes:
        dot.node(
            v.node_id,
            label=_label(view, v),
            **_node_attrs(view, v, fills),
        )
    for v in view.nodes:
        if v.parent_node_id:
            dot.edge(v.parent_node_id, v.node_id)
    return dot.source


def write_tree_graph(view: TreeView, out_dir: str | Path) -> list[Path]:
    """Write tree.dot + render tree.png / tree.svg via the system ``dot``."""
    from graphviz import Digraph

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fills = _objective_fill(view)
    dot = Digraph("research_tree", format="png")
    dot.attr("node", fontname="Helvetica", fontsize="10")
    dot.attr("edge", color="#bbbbbb")
    dot.attr("graph", rankdir="TB", nodesep="0.35", ranksep="0.55")

    for v in view.nodes:
        dot.node(v.node_id, label=_label(view, v), **_node_attrs(view, v, fills))
    for v in view.nodes:
        if v.parent_node_id:
            dot.edge(v.parent_node_id, v.node_id)

    written: list[Path] = []
    dot_path = out_dir / "tree.dot"
    dot_path.write_text(dot.source, encoding="utf-8")
    written.append(dot_path)

    for fmt in ("png", "svg"):
        path = out_dir / f"tree.{fmt}"
        dot.format = fmt
        dot.render(str(out_dir / "tree"), cleanup=True)
        written.append(path)
    return written


def render(run_dir: str | Path, out_dir: str | Path) -> list[Path]:
    return write_tree_graph(load_tree_view(run_dir), out_dir)
