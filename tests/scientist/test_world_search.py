"""The bounded searchers: find_files / search_text carry their own
economics — scan budget, match caps, big/binary skips — so locating a
file never becomes a runaway whole-filesystem scan (the live failure:
find /cvmfs, r4 and r8).
"""
import pytest

from scientist.world import LocalWorld


def _world(tmp_path, **kw) -> LocalWorld:
    for d in ("work", "repo", "scratch"):
        (tmp_path / d).mkdir(exist_ok=True)
    return LocalWorld(work=tmp_path / "work", repo=tmp_path / "repo",
                      scratch=tmp_path / "scratch", **kw)


def test_find_files_matches_name_and_relative_path(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "sub").mkdir()
    (tmp_path / "work" / "steering.yaml").write_text("a: 1")
    (tmp_path / "work" / "sub" / "other.txt").write_text("b: 2")
    result = world.execute(
        {"action": "find_files", "pattern": "*.yaml"})
    assert result["ok"] is True
    assert result["matches"] == ["/work/steering.yaml"]
    assert result["budget_exhausted"] is False
    # a pattern crossing directories matches the relative path
    deep = world.execute(
        {"action": "find_files", "pattern": "sub/*.txt"})
    assert deep["matches"] == ["/work/sub/other.txt"]


def test_search_text_reports_container_paths_and_lines(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "cfg.py").write_text(
        "x = 1\nUseExternalVertex = True\n")
    result = world.execute(
        {"action": "search_text", "pattern": "ExternalVertex"})
    assert result["ok"] is True
    assert result["matches"] == [
        "/work/cfg.py:2:UseExternalVertex = True"]
    assert result["budget_exhausted"] is False


def test_search_text_skips_binary_and_oversized(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "bin.dat").write_bytes(
        b"\x00\x01ExternalVertex\x02")
    # 140k x 15B = 2.1MB of pure matches — over the skip cap
    (tmp_path / "work" / "big.log").write_bytes(
        b"ExternalVertex\n" * 140_000)
    result = world.execute(
        {"action": "search_text", "pattern": "ExternalVertex"})
    assert result["ok"] is True
    assert result["matches"] == []


def test_budget_exhaustion_reports_honestly(tmp_path):
    # a zero-second budget exhausts instantly: the report must say the
    # tree was not covered, not that the file is absent
    world = _world(tmp_path, search_budget_seconds=0.0)
    (tmp_path / "work" / "present.yaml").write_text("a: 1")
    result = world.execute(
        {"action": "find_files", "pattern": "*.yaml"})
    assert result["ok"] is True
    assert result["budget_exhausted"] is True
    assert "unscanned" in result["note"]


def test_root_outside_boundary_resolves_as_mount(tmp_path):
    world = _world(tmp_path)
    outside = tmp_path / "mounted" / "stack"
    outside.mkdir(parents=True)
    (outside / "evtrec.yaml").write_text("c: 3")
    result = world.execute({
        "action": "find_files", "pattern": "*.yaml",
        "root": str(outside)})
    assert result["matches"] == [str(outside / "evtrec.yaml")]


def test_missing_root_reports_cleanly(tmp_path):
    world = _world(tmp_path)
    result = world.execute(
        {"action": "find_files", "pattern": "*",
         "root": "/no/such/root"})
    assert result["ok"] is False
    assert "does not exist" in result["error"]


def test_search_text_glob_filter(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "a.txt").write_text("needle\n")
    (tmp_path / "work" / "b.md").write_text("needle\n")
    result = world.execute({
        "action": "search_text", "pattern": "needle",
        "glob": "*.md"})
    assert result["matches"] == ["/work/b.md:1:needle"]
