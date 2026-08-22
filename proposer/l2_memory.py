"""L2-backed memory service for SimpleEvolution proposers.

Replaces the SimpleLoop MemoryService's experiment ledger (history.jsonl keyed by
round) with queries against the SimpleEvolution SQLite L2 store. Findings remain
a proposer-local concept stored under the episode's session directory.
"""
from __future__ import annotations

from pathlib import Path

from simpleevo.db.queries import ResearchQueries
from simpleevo.scheduler.telemetry import spend_usd


def _query_terms(query: str) -> list[str]:
    """Split a free-text query into lowercased, non-empty substring terms."""
    return [term for term in (query or "").lower().split() if term]


class L2MemoryService:
    """Minimal memory façade backed by L2 for experiments and local files for findings."""

    def __init__(self, run_dir: Path, db_path: Path | None = None):
        self.run_dir = Path(run_dir)
        self.db_path = db_path or (self.run_dir / "simpleevo.db")
        self.queries = ResearchQueries(self.db_path)

    def build_coverage_pack(self, *, current_round: int = 0) -> str:
        """Return aggregate cross-branch coverage without direction text."""
        by_path: dict[str, dict] = {}
        for experiment in self.queries.list_experiments():
            for path in experiment.changed_paths:
                row = by_path.setdefault(
                    path,
                    {
                        "experiments": 0,
                        "gate_passed": 0,
                        "gate_failed": 0,
                        "examples": [],
                    },
                )
                row["experiments"] += 1
                key = (
                    "gate_passed"
                    if experiment.gate_result.passed
                    else "gate_failed"
                )
                row[key] += 1
                row["examples"].append(
                    f"{experiment.experiment_id}@{experiment.parent_node_id}"
                )
        lines = [
            "Coverage map — global experiment coverage, not a direction ranking:",
        ]
        for path in sorted(by_path):
            row = by_path[path]
            examples = ",".join(sorted(row["examples"])[:3])
            lines.append(
                f"- {path}: experiments={row['experiments']} "
                f"gate_passed={row['gate_passed']} "
                f"gate_failed={row['gate_failed']} examples={examples}"
            )
        if not by_path:
            lines.append("- no completed experiment paths recorded")
        return "\n".join(lines)

    def inspect_experiment(self, experiment_id: str) -> dict:
        """Return one world-scoped experiment record."""
        experiment = self.queries.get_experiment(experiment_id)
        if experiment is None:
            return {"ok": False, "error": f"experiment not found: {experiment_id}"}
        proposal = self.queries.get_proposal(experiment.proposal_id)
        parent = self.queries.get_node(experiment.parent_node_id)
        child = (
            self.queries.get_node(experiment.child_node_id)
            if experiment.child_node_id else None
        )
        return {
            "experiment_id": experiment.experiment_id,
            "source_world": {
                "node_id": experiment.parent_node_id,
                "sha": parent.sha if parent else None,
                "metrics": dict(parent.metrics) if parent else {},
            },
            "intervention": {
                "proposal_id": experiment.proposal_id,
                "instruction": proposal.instruction if proposal else None,
                "changed_paths": list(experiment.changed_paths),
            },
            "condition": {
                "recorded_gates": sorted(experiment.gate_result.results),
            },
            "observation": {
                "result_sha": experiment.result_sha,
                "child_node_id": experiment.child_node_id,
                "child_sha": child.sha if child else None,
                "metrics": dict(experiment.metrics),
                "gate": {
                    "passed": experiment.gate_result.passed,
                    "results": {
                        name: {"passed": result.passed, "detail": result.detail}
                        for name, result in experiment.gate_result.results.items()
                    },
                },
                "status": experiment.status,
            },
        }

    def inspect_originating_research_state(self, experiment_id: str) -> dict:
        """Return the attributed memo behind one concrete experiment."""
        experiment = self.queries.get_experiment(experiment_id)
        if experiment is None:
            return {"ok": False, "error": f"experiment not found: {experiment_id}"}
        proposal = self.queries.get_proposal(experiment.proposal_id)
        if proposal is None:
            return {
                "ok": False,
                "error": f"proposal missing for experiment: {experiment_id}",
            }
        if not proposal.research_state_id:
            return {
                "ok": False,
                "error": f"research memo unavailable for experiment: {experiment_id}",
            }
        state = self.queries.get_research_state(proposal.research_state_id)
        if state is None:
            return {
                "ok": False,
                "error": f"research state missing: {proposal.research_state_id}",
            }
        source_node = self.queries.get_node(state.node_id)
        return {
            "ok": True,
            "kind": "SUBJECTIVE_RESEARCH_MEMO",
            "experiment_id": experiment_id,
            "research_state_id": state.research_state_id,
            "source_episode_id": state.episode_id,
            "source_world": {
                "node_id": state.node_id,
                "sha": source_node.sha if source_node else None,
            },
            "working_model": state.working_model,
            "evidence_refs": list(state.evidence_refs),
            "derived_from_research_state_id": state.derived_from_research_state_id,
            "transformation_id": state.transformation_id,
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
                    name: {"passed": result.passed, "detail": result.detail}
                    for name, result in node.gate_result.results.items()
                },
            },
            "depth": node.depth,
            "status": node.status,
            "children": list(children),
        }

    def compare_nodes(self, node_ids: list[str]) -> dict:
        """Return a side-by-side comparison of the requested nodes."""
        nodes = [self.queries.get_node(node_id) for node_id in node_ids]
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
                    "node_id": node.node_id,
                    "sha": node.sha,
                    "depth": node.depth,
                    "metrics": dict(node.metrics),
                    "gate_passed": node.gate_result.passed,
                }
                for node in path
            ],
        }

    def search_experiments(
        self,
        query: str,
        filters: dict | None = None,
        limit: int = 10,
        buckets: bool = True,
    ) -> dict:
        """Coverage query over experiments without returning direction text."""
        query_terms = _query_terms(query)
        rows = []
        for experiment in self.queries.list_experiments():
            if filters:
                if (
                    "gate_passed" in filters
                    and experiment.gate_result.passed != filters["gate_passed"]
                ):
                    continue
                if "status" in filters and experiment.status != filters["status"]:
                    continue
                if "changed_path" in filters:
                    prefix = filters["changed_path"]
                    if not any(
                        path.startswith(prefix)
                        for path in experiment.changed_paths
                    ):
                        continue
            proposal = self.queries.get_proposal(experiment.proposal_id)
            haystack = " ".join([
                proposal.instruction if proposal else "",
                *list(experiment.changed_paths),
            ]).lower()
            if not all(term in haystack for term in query_terms):
                continue
            parent = self.queries.get_node(experiment.parent_node_id)
            rows.append({
                "experiment_id": experiment.experiment_id,
                "source_world": {
                    "node_id": experiment.parent_node_id,
                    "sha": parent.sha if parent else None,
                },
                "child_node_id": experiment.child_node_id,
                "status": experiment.status,
                "gate_passed": experiment.gate_result.passed,
                "metrics": dict(experiment.metrics),
                "changed_paths": list(experiment.changed_paths),
            })
        rows.sort(key=lambda row: row["experiment_id"])
        relevant = rows[:limit]
        if not buckets:
            return {"results": relevant}
        anchor_gate = relevant[0]["gate_passed"] if relevant else None
        contrasting = [
            row for row in rows
            if anchor_gate is not None and row["gate_passed"] != anchor_gate
        ][:limit]
        diverse = []
        seen_paths: set[tuple[str, ...]] = set()
        for row in rows:
            signature = tuple(row["changed_paths"])
            if signature in seen_paths:
                continue
            seen_paths.add(signature)
            diverse.append(row)
            if len(diverse) >= limit:
                break
        return {
            "relevant": relevant,
            "contrasting": contrasting,
            "diverse": diverse,
        }

    def open_allocation_node_ids(self) -> set[str]:
        """Nodes that currently hold an open proposer lease."""
        return self.queries.open_allocation_node_ids()

    def node_allocations(self, node_id: str) -> dict:
        """Proposer investment on one Node plus its public outcomes."""
        allocations = self.queries.allocations_for_node(node_id)
        experiments = [
            {
                "experiment_id": experiment.experiment_id,
                "status": experiment.status,
                "gate_passed": experiment.gate_result.passed,
                "child_node_id": experiment.child_node_id,
                "changed_paths": list(experiment.changed_paths),
            }
            for experiment in self.queries.list_experiments()
            if experiment.parent_node_id == node_id
        ]
        return {
            "ok": True,
            "node_id": node_id,
            "allocations": allocations,
            "experiments": experiments,
        }

    def run_status(self, *, pricing: dict | None = None) -> dict:
        """Mechanical run facts only: no ranking, no recommendation.

        With durable run limits installed (and optionally token pricing for
        the usage ledger) the ``budget`` block carries limit / used /
        remaining / capped so the growth gate can weigh opportunity cost
        against real numbers instead of guessing.
        """
        status = {
            "ok": True,
            "running_attempts": self.queries.running_attempt_counts(),
            "queued_proposals": self.queries.queued_proposal_count(),
            "open_allocations": self.queries.open_allocation_count(),
            "open_experiments": self.queries.open_experiment_count(),
            "node_counts": self.queries.node_status_counts(),
        }
        limits = self.queries.run_limits()
        if not limits and not pricing:
            return status
        terminal = self.queries.terminal_experiment_count()
        spend = (
            spend_usd(self.run_dir, pricing) if pricing else None
        )
        max_evals = limits.get("max_terminal_evals")
        budget_usd = limits.get("budget_usd")
        remaining_evals = (
            None if max_evals is None else max(0, int(max_evals) - terminal)
        )
        remaining_usd = (
            None if budget_usd is None or spend is None
            else round(max(0.0, float(budget_usd) - spend), 6)
        )
        status["budget"] = {
            "max_terminal_evals": max_evals,
            "budget_usd": budget_usd,
            "terminal_evals": terminal,
            "spend_usd": None if spend is None else round(spend, 6),
            "remaining_terminal_evals": remaining_evals,
            "remaining_usd": remaining_usd,
            "capped": (
                remaining_evals == 0
                or (remaining_usd is not None and remaining_usd <= 0.0)
            ),
        }
        return status

    def list_findings(self, state: str = "active", limit: int = 20, **_) -> dict:
        return {"findings": []}

    def search_findings(self, query: str, limit: int = 5) -> dict:
        return {"findings": []}

    def inspect_finding(self, finding_id: str) -> dict:
        return {"ok": False, "error": f"finding not found: {finding_id}"}

    def commit_proposals(self, *, round_id: int | None = None, proposals: list) -> None:
        """No-op in SimpleEvolution MVP: finding attribution is not stored in L2."""
        pass
