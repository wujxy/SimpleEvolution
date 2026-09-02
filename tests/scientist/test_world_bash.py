"""The bash clock: a short default for undeclared commands, a declared
budget for heavyweight runs, a ceiling over both. The default exists
because a cheap-looking whole-tree scan once ate 17 minutes of a live
run's loop (r4: an hour) while seats waited on the PI.
"""
import time

import pytest

from scientist.world import LocalWorld


def _world(tmp_path, **kw) -> LocalWorld:
    work = tmp_path / "work"
    scratch = tmp_path / "scratch"
    work.mkdir(exist_ok=True)
    scratch.mkdir(exist_ok=True)
    (tmp_path / "repo").mkdir(exist_ok=True)
    return LocalWorld(work=work, repo=tmp_path / "repo",
                      scratch=scratch, **kw)


def test_undeclared_command_runs_on_the_short_default(tmp_path):
    world = _world(tmp_path, timeout_seconds=1, timeout_ceiling=60)
    t0 = time.monotonic()
    result = world.execute({"action": "bash", "command": "sleep 30"})
    assert time.monotonic() - t0 < 10
    assert result["timed_out"] is True
    assert result["ok"] is False
    # the timeout report teaches the way out, not just the death
    assert "timeout_seconds" in result["output"]
    assert "1s budget" in result["output"]


def test_declared_budget_runs_past_the_default(tmp_path):
    world = _world(tmp_path, timeout_seconds=1, timeout_ceiling=60)
    result = world.execute(
        {"action": "bash", "command": "sleep 2", "timeout_seconds": 30})
    assert result["timed_out"] is False
    assert result["ok"] is True


def test_declared_budget_is_capped_by_the_ceiling(tmp_path):
    world = _world(tmp_path, timeout_seconds=60, timeout_ceiling=1)
    result = world.execute(
        {"action": "bash", "command": "sleep 30", "timeout_seconds": 9999})
    assert result["timed_out"] is True
    assert "ceiling 1s" in result["output"]


def test_ordinary_command_passes_untouched(tmp_path):
    world = _world(tmp_path)
    result = world.execute({"action": "bash", "command": "echo ok"})
    assert result["ok"] is True
    assert result["timed_out"] is False
    assert "ok" in result["output"]


def test_ceiling_defaults_when_unspecified(tmp_path):
    world = _world(tmp_path, timeout_seconds=5)
    assert world.timeout_ceiling >= world.timeout_seconds
