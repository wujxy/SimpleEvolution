"""Integration test: Scheduler drives proposer → experiment → new node."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from simpleevo.db.store import GateDecision, GateResult, ResearchStore
from simpleevo.scheduler.frontier import FrontierConfig
from simpleevo.scheduler.loop import Scheduler, SchedulerConfig


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        store = ResearchStore(run_dir / "simpleevo.db")
        yield run_dir, store


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_scheduler_closes_proposer_experiment_loop(env):
    run_dir, store = env

    # Seed a root node and thread.
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        thread = tx.create_thread(
            parent_thread_id=None,
            node_id=root.node_id,
            snapshot_ref="",
        )

    config = SchedulerConfig(
        max_proposer_inflight=1,
        max_experiment_inflight=1,
        frontier=FrontierConfig(axes=("total_ms",)),
        poll_seconds=0.0,
    )

    def submit_proposer(allocation_id: str, payload: dict) -> None:
        _write_json(
            run_dir / "proposer_allocations" / allocation_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "proposer",
                "request_id": allocation_id,
                "status": "completed",
                "result": {
                    "thread_id": thread.thread_id,
                    "node_id": root.node_id,
                    "outcome": "submit",
                    "proposals": [
                        {
                            "proposal_id": payload["proposal_ids"][0],
                            "instruction": "inline a small helper to reduce total_ms",
                            "rationale": {"why": "expected faster"},
                        }
                    ],
                },
            },
        )

    experiment_results: dict[str, dict] = {}

    def submit_experiment(experiment_id: str, payload: dict) -> None:
        _write_json(
            run_dir / "experiments" / experiment_id / "result.json",
            {
                "protocol": "simpleevo.worker.v1",
                "kind": "experiment",
                "request_id": experiment_id,
                "status": "completed",
                "result": {
                    "experiment_id": experiment_id,
                    "child_node_id": None,
                    "parent_sha": "sha-root",
                    "sha": "sha-child",
                    "metrics": {"total_ms": 90.0},
                    "gate": {
                        "passed": True,
                        "results": {"PATHS": {"passed": True, "detail": ""}},
                    },
                    "outcome": "COMPLETED",
                    "eval_block": "TOTAL_MS=90.0\nPATHS=pass",
                    "changed_paths": [],
                },
            },
        )
        experiment_results[experiment_id] = payload

    scheduler = Scheduler(
        store, run_dir, config,
        submit_proposer=submit_proposer,
        submit_experiment=submit_experiment,
    )

    # Step 1: allocate proposer, poll result, publish proposals, drain queue,
    # and ingest the synchronous experiment result.
    t1 = scheduler.step()
    assert t1["proposer_jobs"] == 1
    assert t1["published"] == 1
    assert t1["experiment_jobs"] == 1

    # Verify child node was created.
    with store.transaction() as tx:
        children = tx._conn.execute(
            "SELECT * FROM nodes WHERE parent_node_id = ?", (root.node_id,)
        ).fetchall()
    assert len(children) == 1
    assert children[0]["sha"] == "sha-child"
    assert children[0]["metrics"] == '{"total_ms": 90.0}'
