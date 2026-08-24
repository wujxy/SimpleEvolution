"""The Scientist assembles its own worldview at wake time.

Module contract (科学家完整研究制设计 §3): the Scheduler's envelope carries
IDs only; the worker rebuilds every dynamic fact from the store's read-only
view at the moment of consumption — the structural fix for the stale-payload
pathology (席位制设计 §11.1: a queued snapshot ages while the world moves).
"""
from __future__ import annotations

from typing import Any

from simpleevo.db.queries import ResearchQueries
from simpleevo.generator import Generator
from simpleevo.research_state import research_state_to_dict


def seat_block(
    generator_basis: list[Generator], episode,
) -> dict[str, Any] | None:
    """The seat's identity block: the lens this episode was hired under,
    three parts verbatim from the basis.  Frontier-baseline leases carry no
    lens and no seat block."""
    lens_id = getattr(episode, "variation_operator", None)
    if not lens_id:
        return None
    for item in generator_basis:
        if item.id == lens_id:
            return {
                "lens_id": item.id,
                "name_zh": item.name,
                "directive": item.directive or item.description,
                "forbidden": item.forbidden,
                "self_check": item.self_check,
            }
    return {"lens_id": lens_id, "name_zh": lens_id,
            "directive": "", "forbidden": "", "self_check": ""}


def world_transition(queries: ResearchQueries, node) -> dict[str, Any]:
    """The reality record a child Scientist sees on resume (§8).

    ``node.experiment_id`` is the Experiment that produced this Node; its
    facts (metrics / gate / diff) are the world transition from the parent
    the forked Scientist last saw.
    """
    if node.experiment_id is None:
        return {}
    experiment = queries.get_experiment(node.experiment_id)
    if experiment is None:
        return {}
    parent = queries.get_node(experiment.parent_node_id)
    return {
        "parent_node_id": experiment.parent_node_id,
        "experiment_id": experiment.experiment_id,
        "metrics": dict(experiment.metrics),
        "gate": {
            "passed": experiment.gate_result.passed,
            "results": {
                name: {"passed": gr.passed, "detail": gr.detail}
                for name, gr in experiment.gate_result.results.items()
            },
        },
        "diff": list(experiment.changed_paths),
        "parent_metrics": dict(parent.metrics) if parent else {},
    }


def research_state_seed(
    queries: ResearchQueries, node,
) -> dict[str, Any]:
    """Join the one State/Proposal/Experiment path that produced a Child."""
    if node.experiment_id is None:
        return {}
    experiment = queries.get_experiment(node.experiment_id)
    if experiment is None:
        return {}
    proposal = queries.get_proposal(experiment.proposal_id)
    if proposal is None or not proposal.research_state_id:
        return {}
    state = queries.get_research_state(proposal.research_state_id)
    if state is None:
        return {}
    facts = world_transition(queries, node)
    # The author seat's lens: the memo a Child inherits is one school's
    # attributed view, and the attribution travels with it (seat design
    # §2.3 — 署名透镜 makes the discounting structural, not advised).
    author_episode = queries.get_episode(state.episode_id)
    originating_lens = (
        author_episode.variation_operator
        if author_episode is not None else None
    )
    return {
        "originating_lens": originating_lens,
        "child_node": {
            "node_id": node.node_id,
            "sha": node.sha,
            "metrics": dict(node.metrics),
            "gate": {
                "passed": node.gate_result.passed,
                "results": {
                    name: {"passed": result.passed, "detail": result.detail}
                    for name, result in node.gate_result.results.items()
                },
            },
        },
        "originating_research_state": research_state_to_dict(state),
        "proposal": {
            "proposal_id": proposal.proposal_id,
            "instruction": proposal.instruction,
            "expectation": proposal.rationale.get("expectation"),
            "material_difference": proposal.rationale.get(
                "material_difference"
            ),
        },
        "experiment": {
            "experiment_id": experiment.experiment_id,
            "parent_node_id": experiment.parent_node_id,
            "metrics": facts.get("metrics", {}),
            "gate": facts.get("gate", {}),
            "changed_paths": facts.get("diff", []),
            "parent_metrics": facts.get("parent_metrics", {}),
        },
    }


def build_wake_view(
    queries: ResearchQueries,
    generator_basis: list[Generator],
    *,
    node_id: str,
    episode_id: str,
) -> dict[str, Any]:
    """Everything the Scientist's envelope used to carry, rebuilt now.

    Raises if the node or episode vanished (the scheduler resubmits on IDs
    that must exist; a missing row is an infrastructure error, not an
    empty world).
    """
    node = queries.get_node(node_id)
    episode = queries.get_episode(episode_id)
    if node is None:
        raise ValueError(f"unknown node: {node_id}")
    if episode is None:
        raise ValueError(f"unknown episode: {episode_id}")
    view: dict[str, Any] = {
        "node_sha": node.sha,
        "inherited_from_episode_id": episode.inherited_from_episode_id,
        "seat": seat_block(generator_basis, episode),
    }
    seed = research_state_seed(queries, node)
    if seed:
        view["research_state_seed"] = seed
    else:
        view["world_transition"] = world_transition(queries, node)
    # Adjudication write-back (科学家完整研究制 §2.4): a reopened seat reads
    # what the gate rejected and why, at wake, from durable state — the
    # previous_rejection pattern.  Absent on a first attempt.
    adjudication = queries.lease_adjudication_for_episode(episode_id)
    if adjudication is not None:
        view["adjudication_feedback"] = adjudication
        head = queries.research_state_head(episode_id)
        if head is not None and head.conclusion:
            delivered = (head.conclusion or {}).get("delivered_sha")
            if delivered:
                view["adjudication_feedback"]["delivered_world_sha"] = (
                    delivered)
    return view
