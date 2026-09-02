"""The async loop surface: in-batch parallelism with the one-tree lock,
and background bash as a first-class job — handle now, verdict at the
next turn boundary.
"""
import time

from scientist.agent import _run_actions, _background_job_message
from scientist.world import LocalWorld


def _world(tmp_path, **kw) -> LocalWorld:
    for d in ("work", "repo", "scratch"):
        (tmp_path / d).mkdir(exist_ok=True)
    return LocalWorld(work=tmp_path / "work", repo=tmp_path / "repo",
                      scratch=tmp_path / "scratch", **kw)


class _NoSeats:
    """assistant stand-in: no seat dispatch happens in these tests."""

    def __getattr__(self, name):
        raise AssertionError(f"unexpected assistant call: {name}")


class _Ledger:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected ledger call: {name}")


# -- in-batch parallelism ------------------------------------------------

def test_two_bashes_serialize_on_one_tree(tmp_path):
    # one tree, one writer at a time: a batch's bash calls queue on the
    # world-tree lock (shared workdir state, shared build dir). Pin the
    # policy: two 1s bashes cannot interleave into ~1s.
    world = _world(tmp_path)
    actions = [
        {"action": "bash", "tool_call_id": "a",
         "command": "sleep 1; echo one"},
        {"action": "bash", "tool_call_id": "b",
         "command": "sleep 1; echo two"},
    ]
    t0 = time.monotonic()
    results = _run_actions(
        actions, world=world, assistant=_NoSeats(), ledger=_Ledger())
    wall = time.monotonic() - t0
    assert wall >= 1.9, f"two bashes interleaved in {wall:.2f}s"
    by_id = {a["tool_call_id"]: results[id(a)] for a in actions}
    assert "one" in by_id["a"]["output"]
    assert "two" in by_id["b"]["output"]


def test_batch_results_map_to_their_own_calls(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "target.txt").write_text("needle\n")
    actions = [
        {"action": "bash", "tool_call_id": "a", "command": "echo alpha"},
        {"action": "read_file", "tool_call_id": "b",
         "path": "/work/target.txt"},
        {"action": "find_files", "tool_call_id": "c",
         "pattern": "*.txt"},
    ]
    results = _run_actions(
        actions, world=world, assistant=_NoSeats(), ledger=_Ledger())
    by_id = {a["tool_call_id"]: results[id(a)] for a in actions}
    assert "alpha" in by_id["a"]["output"]
    assert "needle" in by_id["b"]["content"]
    assert by_id["c"]["matches"] == ["/work/target.txt"]


def test_malformed_call_bounces_not_crashes(tmp_path):
    world = _world(tmp_path)
    actions = [{"action": "bash", "_arguments_raw": "{not json"}]
    results = _run_actions(
        actions, world=world, assistant=_NoSeats(), ledger=_Ledger())
    assert results[id(actions[0])]["ok"] is False
    assert "not valid JSON" in results[id(actions[0])]["error"]


# -- background bash ------------------------------------------------------

def test_background_job_returns_handle_then_verdict(tmp_path):
    world = _world(tmp_path)
    start = world.execute({
        "action": "bash", "command": "echo verdict-line; sleep 0.4",
        "background": True, "timeout_seconds": 30})
    assert start["ok"] is True and start["background"] is True
    assert start["job_id"].startswith("bashjob-")
    assert world.poll_bash_jobs() == []          # still running
    time.sleep(0.8)
    done = world.poll_bash_jobs()
    assert len(done) == 1
    assert done[0]["ok"] is True
    assert "verdict-line" in done[0]["output"]
    assert world.poll_bash_jobs() == []          # exactly once


def test_background_job_killed_at_its_budget(tmp_path):
    world = _world(tmp_path)
    world.execute({
        "action": "bash", "command": "sleep 30", "background": True,
        "timeout_seconds": 1})
    time.sleep(1.3)
    done = world.poll_bash_jobs()
    assert len(done) == 1
    assert done[0]["timed_out"] is True
    assert done[0]["ok"] is False
    assert "background command was killed" in done[0]["output"]


def test_background_job_cap_refuses_the_fifth(tmp_path):
    world = _world(tmp_path)
    for _ in range(4):
        r = world.execute({
            "action": "bash", "command": "sleep 10", "background": True,
            "timeout_seconds": 60})
        assert r["ok"] is True
    fifth = world.execute({
        "action": "bash", "command": "echo no", "background": True})
    assert fifth["ok"] is False
    assert "already running" in fifth["error"]


def test_background_message_carries_verdict_and_output(tmp_path):
    msg = _background_job_message({
        "job_id": "bashjob-001", "ok": True, "timed_out": False,
        "command": "echo hi", "output": "hi\n", "returncode": 0})
    assert msg.startswith("[background bash | bashjob-001 | ok]")
    assert "command: echo hi" in msg
    assert msg.rstrip().endswith("hi")


def test_evidence_workspace_points_at_the_real_fork(tmp_path):
    # the ack and digest must name the fork path that actually exists;
    # the fossil fresh- prefix sent the PI to a phantom twice in one
    # live run — it found the real workspace only by listing scratch
    from types import SimpleNamespace

    from scientist.assistant_tools import InWorldAssistant

    seat = InWorldAssistant.__new__(InWorldAssistant)
    seat.world = SimpleNamespace(scratch=tmp_path / "scratch",
                                 work=tmp_path / "work")
    fork = tmp_path / "scratch" / "executor-x-001" / "world"
    isolated = seat._evidence_envelope(
        "executor-x-001", fork, 0.0)
    assert isolated["workspace"] == str(fork)
    current = seat._evidence_envelope("executor-x-002", None, 0.0)
    assert current["workspace"] == str(tmp_path / "work")


def test_seat_model_inherits_the_pis_declared_model():
    # one declared model for the whole run: an unspecified
    # assistant.model must fall back to spec.model.model, never to the
    # CLI default resolution (which the endpoint maps to v4-pro — the
    # accident that ran omilrec seats on pro for two live runs)
    from scientist.assistant_tools import AssistantConfig

    inherited = AssistantConfig.from_spec({
        "model": {"model": "deepseek-v4-flash"},
        "assistant": {"command": "claude"}})
    assert inherited.model == "deepseek-v4-flash"

    override = AssistantConfig.from_spec({
        "model": {"model": "deepseek-v4-flash"},
        "assistant": {"command": "claude",
                      "model": "deepseek-v4-pro"}})
    assert override.model == "deepseek-v4-pro"

    assert AssistantConfig.from_spec(
        {"assistant": {"command": "claude"}}).model is None
