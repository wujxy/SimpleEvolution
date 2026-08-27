"""Assistant hands tests: distillation caps, raw on disk, ledger rows."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from simpleevo.contracts import GateDecision
from simpleevo.db.store import ResearchStore
from simpleevo.assistant.hands import (
    AssistantHands, HandTally, _cap_words, _parse_tail,
)
from simpleevo.assistant.lab import Laboratory, snapshot_commit


class _FakeAgent:
    """Records what the real Agent would have been asked to run."""

    instances: list["_FakeAgent"] = []

    def __init__(self, *, world, allowed_tools, timeout_seconds, model,
                 extra_args, usage_observer, trace_store, invocation_id,
                 role, identity, **kwargs):
        self.world = world
        self.allowed_tools = allowed_tools
        self.timeout = timeout_seconds
        self.identity = identity
        self.prompt: str | None = None
        self.world_cwd = None
        _FakeAgent.instances.append(self)

    def run_text(self, prompt, *, cwd, label, world_cwd=None):
        self.prompt = prompt
        self.world_cwd = world_cwd
        if self.identity["kind"] == "consult":
            return (
                "thinking… ```json\n"
                '{"answer_digest": "prior art exists: bucketed indexes.", '
                '"sources": ["one-line source"]}\n```'
            )
        return (
            "did the work\n```json\n"
            '{"diff_summary": "swapped binary search for bucket index", '
            '"self_report_digest": "implemented, self-measured", '
            '"metrics": {"lps": 1620000}}\n```'
        )


@pytest.fixture()
def env(tmp_path):
    _FakeAgent.instances = []
    run_dir = tmp_path
    store = ResearchStore(run_dir / "t.db")
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="sha-r",
            metrics={}, gate_result=GateDecision({}, True), depth=0,
            status="active",
        )
        episode = tx.create_episode(node_id=root.node_id)
    # A tiny real git repo so the lab's side-chain snapshots are real.
    repo = run_dir / "repo"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "main.c").write_text("int main(){return 0;}\n")
    git("add", "-A")
    git("commit", "-qm", "root")
    node_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    from simpleevo.assistant.git_worktree import GitWorkspaceProvider

    provider = GitWorkspaceProvider(run_dir, repo)
    provider.initialize()
    lab = Laboratory(
        provider=provider, episode_id=episode.episode_id,
        node_sha=node_sha, editable_paths=("src",),
    )
    hands = AssistantHands(
        run_dir=run_dir, db_path=store.path,
        lease_id="alloc-1", episode_id=episode.episode_id,
        node_id=root.node_id, node_sha=node_sha, lens="lens-x",
        lab=lab, runtime_image=run_dir / "img.sif",
        executor_cfg={"base_url": None, "effort": "low"},
        editable_paths=("src",),
        agent_factory=_FakeAgent,
    )
    return run_dir, store, root, episode, hands, lab, node_sha, repo


def test_cap_words_truncates_at_boundary():
    text = " ".join(f"w{i}" for i in range(50))
    capped, truncated = _cap_words(text, 10)
    assert truncated is True
    assert capped.split()[:10] == text.split()[:10]
    assert "truncated at cap" in capped
    assert _cap_words("short text", 10) == ("short text", False)


def test_parse_tail_takes_last_json_fence():
    text = 'noise ```json\n{"a": 1}\n``` more ```json\n{"b": 2}\n```'
    assert _parse_tail(text) == {"b": 2}
    assert _parse_tail("no fences") is None


def test_consult_returns_distilled_belief_and_persists_raw(env):
    (run_dir, store, root, episode, hands, lab, node_sha, repo) = env
    out = hands.consult(
        "is there prior art?", context="I measure 3 misses/lookup",
        read="none",
    )
    assert out["channel"] == "belief"
    assert out["answer_digest"] == "prior art exists: bucketed indexes."
    assert out["truncated"] is False
    agent = _FakeAgent.instances[-1]
    # No write tools on the consult channel — the permission boundary is
    # in the tool list, not just the mounts.
    assert "Edit" not in agent.allowed_tools
    assert "Write" not in agent.allowed_tools
    assert "Bash" not in agent.allowed_tools
    assert "WebSearch" in agent.allowed_tools
    assert agent.world_cwd is None or str(agent.world_cwd) == "/"
    # Raw on disk, addressable by call id.
    raw_dir = run_dir / "assistant" / out["call_id"]
    assert (raw_dir / "prompt.txt").exists()
    assert (raw_dir / "raw.txt").exists()
    digest = json.loads((raw_dir / "digest.json").read_text())
    assert digest["answer_digest"] == out["answer_digest"]
    # The ledger row exists, by lens.
    with store.transaction() as tx:
        row = tx._conn.execute(
            "SELECT * FROM assistant_calls WHERE call_id = ?",
            (out["call_id"],),
        ).fetchone()
    assert row["kind"] == "consult"
    assert row["lens"] == "lens-x"
    assert row["adopted"] is None


def test_work_snapshots_lab_and_ledgers_occupancy(env):
    (run_dir, store, root, episode, hands, lab, node_sha, repo) = env
    # The assistant's fake run does not edit files; put a lab edit in by
    # hand so the snapshot has something to freeze.
    lab_ws = lab.main()
    (lab_ws.path / "src" / "main.c").write_text(
        "int main(){return 1;}\n")
    out = hands.work("implement bucket index", mode="continue",
                     budget_minutes=5)
    assert out["status"] == "done"
    assert out["world_sha"] is not None
    assert out["metrics"] == {"lps": 1620000}
    assert out["world_sha"] != node_sha
    # Resource ledger: one closed work row.
    with store.transaction() as tx:
        rows = tx._conn.execute(
            "SELECT * FROM resource_ledger WHERE ref_id = ?",
            (out["call_id"],),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "work"
    assert rows[0]["closed_at"] is not None
    # Usage + ledger for the work call.
    with store.transaction() as tx:
        call = tx._conn.execute(
            "SELECT kind, world_sha FROM assistant_calls WHERE call_id = ?",
            (out["call_id"],),
        ).fetchone()
    assert call["kind"] == "work"
    assert call["world_sha"] == out["world_sha"]


def test_snapshot_stages_only_editable_paths(env):
    (run_dir, store, root, episode, hands, lab, node_sha, repo) = env
    lab_ws = lab.main()
    (lab_ws.path / "src" / "main.c").write_text("// changed\n")
    (lab_ws.path / "PROGRESS.md").write_text("scratch, never in a node")
    sha = lab.snapshot("t1")
    assert sha is not None
    changed = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only",
         f"{node_sha}..{sha}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == ["src/main.c"]


def test_distillation_cap_enforced_on_garbage_tail(env):
    (run_dir, store, root, episode, hands, lab, node_sha, repo) = env

    class _Rambling(_FakeAgent):
        def run_text(self, prompt, *, cwd, label, world_cwd=None):
            return " ".join(f"word{i}" for i in range(500))

    hands.agent_factory = _Rambling
    out = hands.consult("q")
    assert out["truncated"] is True
    assert "truncated at cap" in out["answer_digest"]


def test_call_budget_refusal(env):
    (run_dir, store, root, episode, hands, lab, node_sha, repo) = env
    hands.tally = HandTally(max_consult_calls=0, max_work_calls=0)
    assert hands.consult("q")["status"] == "refused"
    assert hands.work("x")["status"] == "refused"
