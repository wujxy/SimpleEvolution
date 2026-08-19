"""Terminal ASCII tree renderer (① system-status view).

Answers the developer's most frequent question — "is the tree growing the way
I think it is?" — with zero dependencies. Each node gets one line: a short
node id, its objective value, and (when present) status and frontier marks.
"""
from __future__ import annotations

from .data import TreeView, load_tree_view


def _short(node_id: str, n: int = 8) -> str:
    return node_id[:n]


def _glyph(v) -> str:
    if v.frontier_axes:
        return "●"  # current frontier winner
    if v.status == "dead":
        return "✗"
    if v.status == "dormant":
        return "~"
    return "○"  # active, off-frontier


def _label(view: TreeView, v) -> str:
    if v.objective is None:
        objective = "objective=—"
    else:
        objective = f"{view.objective_key}={v.objective:.4g}"
    marks: list[str] = []
    if v.status != "active":
        marks.append(v.status)
    if v.frontier_axes:
        marks.append("F:" + ",".join(sorted(v.frontier_axes)))
    suffix = ("  [" + " ".join(marks) + "]") if marks else ""
    return f"{_short(v.node_id)}  {objective}{suffix}"


def render_tree(view: TreeView) -> str:
    """Render the whole research tree as an ASCII string."""
    root_ids = [v.node_id for v in view.nodes if v.parent_node_id is None]
    if not root_ids:
        root_ids = [v.node_id for v in view.nodes if v.depth == 0]
    if not root_ids and view.nodes:
        root_ids = [view.nodes[0].node_id]

    lines: list[str] = []

    def walk(node_id: str, prefix: str, is_last: bool, is_root: bool) -> None:
        v = view.by_id[node_id]
        branch = "" if is_root else ("└─ " if is_last else "├─ ")
        lines.append(f"{prefix}{branch}{_glyph(v)} {_label(view, v)}")
        kids = view.children.get(node_id, ())
        child_prefix = prefix + ("" if is_root else ("    " if is_last else "│   "))
        for i, cid in enumerate(kids):
            walk(cid, child_prefix, i == len(kids) - 1, False)

    for i, rid in enumerate(root_ids):
        walk(rid, "", i == len(root_ids) - 1, True)

    header = (
        f"tree: {len(view.nodes)} node(s), objective={view.objective_key} "
        f"({'lower' if view.lower_is_better else 'higher'}-is-better)\n"
        f"● = frontier winner  ○ = active  ✗ = dead  ~ = dormant"
    )
    return header + "\n" + "\n".join(lines)


def render(run_dir: str) -> str:
    return render_tree(load_tree_view(run_dir))
