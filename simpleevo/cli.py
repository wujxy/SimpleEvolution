"""SimpleEvolution CLI."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import EvolutionConfig, load_config, save_config
from .db.queries import ResearchQueries
from .db.store import GateDecision, ResearchStore
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
    """Seed a root node and thread if the research tree is empty."""
    queries = ResearchQueries(store.path)
    if queries.list_nodes():
        return

    root_sha = _resolve_root_sha(config)
    gate = GateDecision({}, True)
    metrics: dict = {}
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
        tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
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
        from experiment.apptainer import (
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
        queue=QueueConfig(max_size=config.queue_max_size),
        poll_seconds=config.poll_seconds,
        quiescence_window_proposals=config.quiescence_window_proposals,
    )


def _run_scheduler(
    config: EvolutionConfig,
    run_dir: Path,
    max_steps: int | None,
) -> int:
    store = ResearchStore(run_dir / "simpleevo.db")
    scheduler_config = _build_scheduler_config(config)
    submitter = LocalSubmitter(run_dir, config)
    scheduler = Scheduler(
        store,
        run_dir,
        scheduler_config,
        evolution_config=config,
        submit_proposer=submitter.submit_proposer,
        submit_experiment=submitter.submit_experiment,
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
    return _run_scheduler(config, run_dir, args.max_steps)


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
    return _run_scheduler(config, run_dir, args.max_steps)


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
        tx.create_thread(
            parent_thread_id=None,
            node_id=node.node_id,
            snapshot_ref="",
        )
    print(f"reseeded node {node.node_id} with fresh thread")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simpleevo")
    parser.add_argument("--run-dir", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="prepare git + image + run-dir + root node")
    init_p.add_argument("--config", required=True, type=Path)
    init_p.set_defaults(func=_cmd_init)

    run_p = sub.add_parser("run", help="start the evolution (config required)")
    run_p.add_argument("--config", required=True, type=Path)
    run_p.add_argument("--max-steps", type=int, default=None)
    run_p.set_defaults(func=_cmd_run)

    resume_p = sub.add_parser("resume", help="continue an existing run")
    resume_p.add_argument("--max-steps", type=int, default=None)
    resume_p.set_defaults(func=_cmd_resume)

    status_p = sub.add_parser("status", help="show current research state")
    status_p.set_defaults(func=_cmd_status)

    inspect_p = sub.add_parser("inspect", help="inspect a node")
    inspect_p.add_argument("--node", required=True)
    inspect_p.set_defaults(func=_cmd_inspect)

    reseed_p = sub.add_parser("reseed", help="attach a fresh thread to a node")
    reseed_p.add_argument("--node", required=True)
    reseed_p.set_defaults(func=_cmd_reseed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
