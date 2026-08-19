"""L2-backed memory service for SimpleEvolution proposers.

Replaces the SimpleLoop MemoryService's experiment ledger (history.jsonl keyed by
round) with queries against the SimpleEvolution SQLite L2 store.  Findings remain
a proposer-local concept stored under the episode's session directory.
"""
from __future__ import annotations

from pathlib import Path

from simpleevo.db.queries import ResearchQueries


def _query_terms(query: str) -> list[str]:
    """Split a free-text query into lowercased, non-empty substring terms."""
    return [term for term in (query or "").lower().split() if term]


class L2MemoryService:
    """Minimal memory façade backed by L2 for experiments and local files for findings."""

    def __init__(self, run_dir: Path, db_path: Path | None = None):
        self.run_dir = Path(run_dir)
        self.db_path = db_path or (self.run_dir / "simpleevo.db")
        self.queries = ResearchQueries(self.db_path)

    # ------------------------------------------------------------------
    # Coverage pack (L2-backed, minimal MVP)
    # ------------------------------------------------------------------

    def build_coverage_pack(self, *, current_round: int = 0) -> str:
        """Return a minimal coverage text from L2 experiments."""
        nodes = self.queries.list_nodes()
        lines = [
            f"- {n.node_id}: sha={n.sha[:10]} depth={n.depth} "
            f"gate={n.gate_result.passed} metrics={dict(n.metrics)}"
            for n in nodes
        ]
        return "Coverage map (authoritative L2 facts):\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # Experiment tools (L2-backed)
    # ------------------------------------------------------------------

    def inspect_experiment(self, experiment_id: str) -> dict:
        """Return one experiment by its experiment_id."""
        experiment = self.queries.get_experiment(experiment_id)
        if experiment is None:
            return {"ok": False, "error": f"experiment not found: {experiment_id}"}
        parent = self.queries.get_node(experiment.parent_node_id)
        child = (
            self.queries.get_node(experiment.child_node_id)
            if experiment.child_node_id else None
        )
        return {
            "experiment_id": experiment.experiment_id,
            "proposal_id": experiment.proposal_id,
            "parent_node_id": experiment.parent_node_id,
            "parent_sha": parent.sha if parent else None,
            "result_sha": experiment.result_sha,
            "child_node_id": experiment.child_node_id,
            "child_sha": child.sha if child else None,
            "metrics": dict(experiment.metrics),
            "gate": {
                "passed": experiment.gate_result.passed,
                "results": {
                    name: {"passed": gr.passed, "detail": gr.detail}
                    for name, gr in experiment.gate_result.results.items()
                },
            },
            "status": experiment.status,
            "parent_metrics": parent.metrics if parent else {},
        }

    def inspect_node(self, node_id: str) -> dict:
        """Return one node and its direct children."""
        node = self.queries.get_node(node_id)
        if node is None:
            return {"ok": False, "error": f"node not found: {node_id}"}
        tree = self.queries.tree()
        entry = tree.get(node_id)
        children = entry.children if entry else ()
        return {
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "experiment_id": node.experiment_id,
            "sha": node.sha,
            "metrics": dict(node.metrics),
            "gate": {
                "passed": node.gate_result.passed,
                "results": {
                    name: {"passed": gr.passed, "detail": gr.detail}
                    for name, gr in node.gate_result.results.items()
                },
            },
            "depth": node.depth,
            "status": node.status,
            "children": list(children),
        }

    def compare_nodes(self, node_ids: list[str]) -> dict:
        """Return a side-by-side comparison of the requested nodes."""
        nodes = [self.queries.get_node(nid) for nid in node_ids]
        rows = []
        for node in nodes:
            if node is None:
                continue
            rows.append({
                "node_id": node.node_id,
                "sha": node.sha,
                "depth": node.depth,
                "metrics": dict(node.metrics),
                "gate_passed": node.gate_result.passed,
                "status": node.status,
            })
        return {"ok": True, "nodes": rows}

    def lineage(self, node_id: str) -> dict:
        """Return the root-to-node lineage."""
        path = self.queries.node_lineage(node_id)
        return {
            "ok": True,
            "node_id": node_id,
            "path": [
                {
                    "node_id": n.node_id,
                    "sha": n.sha,
                    "depth": n.depth,
                    "metrics": dict(n.metrics),
                    "gate_passed": n.gate_result.passed,
                }
                for n in path
            ],
        }

    def search_experiments(
        self,
        query: str,
        filters: dict | None = None,
        limit: int = 10,
        buckets: bool = True,
    ) -> dict:
        """Coverage query over experiments.

        ``query`` matches against a proposal's instruction / rationale text and
        the changed paths (case-insensitive substring).  Filters stack as AND.
        """
        query_terms = _query_terms(query)
        experiments = self.queries.list_experiments()
        rows = []
        for exp in experiments:
            if filters:
                if "gate_passed" in filters and exp.gate_result.passed != filters["gate_passed"]:
                    continue
                if "status" in filters and exp.status != filters["status"]:
                    continue
                if "changed_path" in filters:
                    prefix = filters["changed_path"]
                    if not any(p.startswith(prefix) for p in exp.changed_paths):
                        continue
            proposal = self.queries.get_proposal(exp.proposal_id)
            haystack = " ".join(
                [proposal.instruction if proposal else "", *list(exp.changed_paths)]
            ).lower()
            if not all(term in haystack for term in query_terms):
                continue
            node = self.queries.get_node(exp.child_node_id or exp.parent_node_id)
            rows.append({
                "experiment_id": exp.experiment_id,
                "parent_node_id": exp.parent_node_id,
                "child_node_id": exp.child_node_id,
                "status": exp.status,
                "gate_passed": exp.gate_result.passed,
                "metrics": dict(exp.metrics),
                "changed_paths": list(exp.changed_paths),
                "sha": node.sha if node else None,
            })
        rows = rows[:limit]
        if buckets:
            return {
                "relevant": rows,
                "contrasting": [],
                "diverse": [],
            }
        return {"results": rows}

    # ------------------------------------------------------------------
    # Finding tools (stubbed: findings are proposer-local, not in L2 MVP)
    # ------------------------------------------------------------------

    def list_findings(self, state: str = "active", limit: int = 20, **_) -> dict:
        return {"findings": []}

    def search_findings(self, query: str, limit: int = 5) -> dict:
        return {"findings": []}

    def inspect_finding(self, finding_id: str) -> dict:
        return {"ok": False, "error": f"finding not found: {finding_id}"}

    def commit_proposals(self, *, round_id: int | None = None, proposals: list) -> None:
        """No-op in SimpleEvolution MVP: finding attribution is not stored in L2."""
        pass
