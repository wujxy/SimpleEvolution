"""SimpleEvolution CLI."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import EvolutionConfig, load_config, save_config
from .db.queries import ResearchQueries
from .db.store import GateDecision, ResearchStore
from .jobs.base import BaseSubmitter
from .jobs.condor import HTCondorSubmitter
from .jobs.local import LocalSubmitter
from .scheduler.loop import Scheduler, SchedulerConfig
from .scheduler.queue import QueueConfig


def _resolve_root_sha(config: EvolutionConfig) -> str:
    """Return the configured root_sha, or the repo's current HEAD."""
    if config.root_sha:
        return config.root_sha
    result = subprocess.run(
        ["git", "-C", str(config.repo_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    raise ValueError(
        f"repo {config.repo_path} has no HEAD commit; "
        "set root_sha in the task config or create an initial commit"
    )


def _ensure_root_node(store: ResearchStore, config: EvolutionConfig) -> None:
    """Seed a root node and its fresh Scientist episode if the tree is empty.

    One Node = one Scientist episode (§3.4): by default the root gets exactly
    one fresh episode, which submits up to ``proposal_slots`` proposals in its
    single batch.  Diversity comes from that batch spanning distinct
    directions and from forked children — NOT from seeding several independent
    root Scientists, which would each re-derive the same hotspot and collapse
    the tree.  ``root_fresh_scientists`` is kept (default 1) for backward
    compatibility only.
    """
    queries = ResearchQueries(store.path)
    if queries.list_nodes():
        return

    root_sha = _resolve_root_sha(config)
    gate = GateDecision({}, True)
    metrics: dict = {}
    n_episodes = max(1, config.root_fresh_scientists)
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha=root_sha,
            metrics=metrics,
            gate_result=gate,
            depth=0,
            status="active",
        )
        for _ in range(n_episodes):
            tx.create_episode(
                node_id=root.node_id,
            )


def _prepare_git(config: EvolutionConfig) -> str:
    """Ensure the source repo is a git repository with a HEAD commit.

    Returns "ready" when the repo already has a HEAD, "initialized" when a
    baseline commit was freshly created from the working tree.
    """
    repo = config.repo_path
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found on host")
    if not repo.is_dir():
        raise RuntimeError(f"repo path does not exist: {repo}")

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [git, "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    toplevel = _run("rev-parse", "--show-toplevel")
    head = _run("rev-parse", "--verify", "HEAD^{commit}")
    is_root = (
        toplevel.returncode == 0
        and Path(toplevel.stdout.strip()).resolve() == repo.resolve()
    )
    if is_root and head.returncode == 0:
        return "ready"

    if not is_root:
        _run("init")
    _run("add", "-A")
    _run(
        "-c", "user.name=SimpleEvolution",
        "-c", "user.email=evo@example.invalid",
        "commit", "--allow-empty", "-m", "SimpleEvolution baseline",
    )
    return "initialized"


def _preflight_image(config: EvolutionConfig) -> str:
    """Best-effort runtime image check; returns a human-readable status.

    The image itself is built out-of-band (the example's ``setup.sh``); this
    only verifies it exists and, when Apptainer is present, that it is usable.
    """
    image = config.runtime_image
    if not image.is_file() or not os.access(image, os.R_OK):
        return "missing (build it first)"
    try:
        from simpleevo.runtime import (
            ApptainerSandbox,
            SandboxSpec,
            evaluator_environment,
        )
    except Exception:
        return "ready (Apptainer preflight skipped)"
    try:
        ApptainerSandbox(userns=True).preflight(
            SandboxSpec(image, evaluator_environment(), True)
        )
        return "ready"
    except Exception as exc:
        return f"preflight failed: {exc}"


def _measure_baseline(
    config: EvolutionConfig, run_dir: Path, root_sha: str,
    submitter: BaseSubmitter,
) -> dict:
    """Evaluate the pristine source once — the run-start baseline.

    Submitted through the SAME job backend as every candidate experiment
    (SimpleLoop's hepjob contract): under condor the baseline is itself a
    condor job, so the anchor objective is measured on the pinned machine
    class alongside — not on the login node beside — the candidates it will
    be compared against.  The payload carries ``eval_only``: the experiment
    worker skips its executor (the pristine tree needs no implementation
    agent) and runs only the eval commands.  Gates and objective are
    validated by the worker; this side additionally rejects a failed /
    missing result so a run that cannot measure its own baseline aborts
    loudly instead of optimizing blind.
    """
    from .jobs.envelope import WorkerStatus, read_result

    run_id = "baseline"
    # A previous attempt's result would be read as this one's: the baseline
    # runs at most once per run (root metrics guard), but a retried init
    # after a timeout must not silently reuse stale metrics.
    result_path = run_dir / "experiments" / run_id / "result.json"
    result_path.unlink(missing_ok=True)
    submitted = Path(submitter.submit_baseline(run_id, {
        "experiment_id": run_id,
        "proposal_id": "",
        "parent_node_id": "",
        "parent_sha": root_sha,
        "proposal": "baseline evaluation",
        "eval_only": True,
    }))
    deadline = time.monotonic() + config.jobs.run_timeout_seconds
    while not submitted.exists():
        if time.monotonic() > deadline:
            submitter.remove_job(run_id, "experiment")
            raise RuntimeError(
                "baseline job did not finish within "
                f"run_timeout_seconds={config.jobs.run_timeout_seconds}s"
            )
        time.sleep(config.jobs.poll_seconds)
    result = read_result(submitted)
    if result.status != WorkerStatus.COMPLETED:
        raise RuntimeError(
            f"baseline evaluation failed: {result.error or result.status.value}"
        )
    payload = result.result
    if payload.get("outcome") not in ("COMPLETED",):
        raise RuntimeError(
            f"baseline evaluation did not complete: "
            f"{payload.get('outcome')} ({payload.get('reason', '')})"
        )
    metrics = dict(payload.get("metrics") or {})
    objective = (config.metrics_schema or {}).get("objective") or {}
    objective_key = str(objective.get("key", "OBJECTIVE"))
    value = metrics.get(objective_key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        block = str(payload.get("eval_block") or "")[:8000]
        raise RuntimeError(
            f"baseline objective {objective_key} is missing or not "
            f"finite:\n{block}"
        )
    failed_gates = [
        str(g.get("key")) for g in (config.metrics_schema or {}).get("gates") or []
        if metrics.get(str(g.get("key"))) is not True
    ]
    if failed_gates:
        raise RuntimeError(
            "baseline gate(s) did not pass: " + ", ".join(failed_gates)
        )
    return metrics


def _ensure_baseline_measured(
    config: EvolutionConfig, run_dir: Path, store: ResearchStore,
) -> None:
    """Measure the unmodified baseline once, at run start.

    ``init`` seeds the root node with empty metrics; the evolution loop needs
    the pristine source's objective as the relative anchor for every
    improvement plot and for the frontier's first comparison. This evaluates
    the root SHA before the first proposer allocation and stores the metrics on
    the root node. Resume skips it: once measured, the baseline is fixed.
    """
    queries = ResearchQueries(store.path)
    root = queries.root_node()
    if root is None or root.metrics:
        return
    submitter = _build_submitter(config, run_dir)
    print(f"[scheduler] evaluating baseline on {root.sha[:10]}...", flush=True)
    metrics = _measure_baseline(config, run_dir, root.sha, submitter)
    store.set_node_metrics(root.node_id, metrics)
    print(f"[scheduler] baseline eval done: {json.dumps(metrics)}", flush=True)


def _init_run(config: EvolutionConfig, run_dir: Path) -> dict:
    """Prepare git + image + run-dir + root node for a task config."""
    repo_status = _prepare_git(config)
    image_status = _preflight_image(config)
    run_dir.mkdir(parents=True, exist_ok=True)
    save_config(run_dir / "task.yaml", config)
    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_root_node(store, config)
    return {"repo_status": repo_status, "image_status": image_status}


def _build_scheduler_config(config: EvolutionConfig) -> SchedulerConfig:
    """Map EvolutionConfig fields to SchedulerConfig."""
    return SchedulerConfig(
        max_proposer_inflight=config.max_proposer_inflight,
        max_experiment_inflight=config.max_experiment_inflight,
        proposal_slots=config.proposal_slots,
        queue=QueueConfig(max_size=config.queue_max_size),
        poll_seconds=config.poll_seconds,
        quiescence_window_proposals=config.quiescence_window_proposals,
    )


def _build_submitter(
    config: EvolutionConfig,
    run_dir: Path,
) -> BaseSubmitter:
    """Construct the job backend selected by ``config.jobs.backend``.

    Local and Condor implement the same BaseSubmitter interface; swapping the
    backend is a config change, not a code change (§13).
    """
    if config.jobs.backend == "condor":
        return HTCondorSubmitter(run_dir, config)
    return LocalSubmitter(run_dir, config)


def _run_scheduler(
    config: EvolutionConfig,
    run_dir: Path,
    max_steps: int | None,
    max_evals: int | None = None,
    budget_usd: float | None = None,
) -> int:
    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_baseline_measured(config, run_dir, store)
    scheduler_config = _build_scheduler_config(config)
    if max_evals is not None or budget_usd is not None:
        # Durable budget policy (run_limits table): a resubmit storm or a
        # runaway spend stops the run at the cap instead of looping forever.
        scheduler_config = replace(
            scheduler_config,
            max_terminal_evals=max_evals,
            budget_usd=budget_usd,
        )
    submitter = _build_submitter(config, run_dir)
    scheduler = Scheduler(
        store,
        run_dir,
        scheduler_config,
        evolution_config=config,
        submitter=submitter,
    )
    summary = scheduler.run(max_steps=max_steps)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_dir = Path(args.run_dir)
    status = _init_run(config, run_dir)
    print(f"Initializing: {args.config}")
    print(f"  Git source: {status['repo_status']} ({config.repo_path})")
    print(f"  Apptainer image: {status['image_status']} ({config.runtime_image})")
    print(f"  Run dir: {run_dir}")
    print("Ready to run.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    run_dir = Path(args.run_dir)
    _init_run(config, run_dir)
    return _run_scheduler(
        config, run_dir, args.max_steps,
        max_evals=args.max_evals, budget_usd=args.budget_usd,
    )


def _cmd_resume(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    config_path = run_dir / "task.yaml"
    if not config_path.exists():
        print(
            f"no saved task.yaml in {run_dir}; run `init`/`run --config` first",
            file=sys.stderr,
        )
        return 1
    config = load_config(config_path)
    # Reconcile and continue; do not rebuild git/image.
    store = ResearchStore(run_dir / "simpleevo.db")
    _ensure_root_node(store, config)
    return _run_scheduler(
        config, run_dir, args.max_steps,
        max_evals=args.max_evals, budget_usd=args.budget_usd,
    )


def _cmd_status(args: argparse.Namespace) -> int:
    queries = ResearchQueries(Path(args.run_dir) / "simpleevo.db")
    nodes = queries.list_nodes()
    frontier = queries.frontier_nodes()
    queued = queries.queued_proposals()
    print(f"nodes: {len(nodes)}")
    print(f"frontier: {len(frontier)}")
    print(f"queued proposals: {len(queued)}")
    for node in frontier:
        print(f"  {node.node_id}: sha={node.sha[:10]} metrics={dict(node.metrics)}")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    queries = ResearchQueries(Path(args.run_dir) / "simpleevo.db")
    node = queries.get_node(args.node)
    if node is None:
        print(f"node not found: {args.node}", file=sys.stderr)
        return 1
    print(json.dumps({
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
    }, indent=2))
    return 0


def _cmd_reseed(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    store = ResearchStore(run_dir / "simpleevo.db")
    queries = ResearchQueries(store.path)
    node = queries.get_node(args.node)
    if node is None:
        print(f"node not found: {args.node}", file=sys.stderr)
        return 1
    with store.transaction() as tx:
        tx.create_episode(
            node_id=node.node_id,
        )
    print(f"reseeded node {node.node_id} with fresh episode")
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    from .reporting.ascii import render

    print(render(str(args.run_dir)))
    return 0


def _cmd_plot(args: argparse.Namespace) -> int:
    from .reporting.plots import render

    out_dir = args.out_dir or (Path(args.run_dir) / "reports")
    for path in render(str(args.run_dir), out_dir):
        print(f"wrote {path}")
    return 0


def _cmd_tree_graph(args: argparse.Namespace) -> int:
    from .reporting.graphviz_tree import render

    out_dir = args.out_dir or (Path(args.run_dir) / "reports")
    for path in render(str(args.run_dir), out_dir):
        print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simpleevo")
    sub = parser.add_subparsers(dest="command", required=True)

    # ``--run-dir`` is accepted both before the subcommand
    # (``simpleevo --run-dir runs/x init ...``) and after it
    # (``simpleevo init --config ... --run-dir runs/x``). A shared parent
    # parser adds it to every subcommand, and a top-level copy covers the
    # pre-subcommand position. ``argparse.SUPPRESS`` defaults stop whichever
    # position did not supply the value from clobbering the one that did.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, default=argparse.SUPPRESS)

    init_p = sub.add_parser(
        "init", parents=[common], help="prepare git + image + run-dir + root node"
    )
    init_p.add_argument("--config", required=True, type=Path)
    init_p.set_defaults(func=_cmd_init)

    run_p = sub.add_parser(
        "run", parents=[common], help="start the evolution (config required)"
    )
    run_p.add_argument("--config", required=True, type=Path)
    run_p.add_argument("--max-steps", type=int, default=None)
    run_p.add_argument("--max-evals", type=int, default=None,
                       help="durable cap on terminal experiments")
    run_p.add_argument("--budget-usd", type=float, default=None,
                       help="durable spend cap (token pricing)")
    run_p.set_defaults(func=_cmd_run)

    resume_p = sub.add_parser(
        "resume", parents=[common], help="continue an existing run"
    )
    resume_p.add_argument("--max-steps", type=int, default=None)
    resume_p.add_argument("--max-evals", type=int, default=None,
                          help="durable cap on terminal experiments")
    resume_p.add_argument("--budget-usd", type=float, default=None,
                          help="durable spend cap (token pricing)")
    resume_p.set_defaults(func=_cmd_resume)

    status_p = sub.add_parser(
        "status", parents=[common], help="show current research state"
    )
    status_p.set_defaults(func=_cmd_status)

    inspect_p = sub.add_parser(
        "inspect", parents=[common], help="inspect a node"
    )
    inspect_p.add_argument("--node", required=True)
    inspect_p.set_defaults(func=_cmd_inspect)

    reseed_p = sub.add_parser(
        "reseed", parents=[common], help="attach a fresh episode to a node"
    )
    reseed_p.add_argument("--node", required=True)
    reseed_p.set_defaults(func=_cmd_reseed)

    tree_p = sub.add_parser(
        "tree", parents=[common], help="print the research tree as ASCII"
    )
    tree_p.set_defaults(func=_cmd_tree)

    plot_p = sub.add_parser(
        "plot", parents=[common], help="write progress/pareto PNGs"
    )
    plot_p.add_argument(
        "--out-dir", type=Path, default=None,
        help="output dir (default: <run-dir>/reports)",
    )
    plot_p.set_defaults(func=_cmd_plot)

    tree_graph_p = sub.add_parser(
        "tree-graph", parents=[common],
        help="write Graphviz tree (.dot/.png/.svg)",
    )
    tree_graph_p.add_argument(
        "--out-dir", type=Path, default=None,
        help="output dir (default: <run-dir>/reports)",
    )
    tree_graph_p.set_defaults(func=_cmd_tree_graph)

    args = parser.parse_args(argv)
    if not hasattr(args, "run_dir"):
        parser.error("the following arguments are required: --run-dir")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
