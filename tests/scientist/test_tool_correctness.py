"""Correctness of the generic tool surface: the verdict survives
truncation, reads stream only their window, writes land atomically.
"""
from scientist.world import LocalWorld


def _world(tmp_path, **kw) -> LocalWorld:
    for d in ("work", "repo", "scratch"):
        (tmp_path / d).mkdir(exist_ok=True)
    return LocalWorld(work=tmp_path / "work", repo=tmp_path / "repo",
                      scratch=tmp_path / "scratch", **kw)


def test_bash_truncation_keeps_head_and_tail(tmp_path):
    world = _world(tmp_path, cap_chars=400)
    result = world.execute({
        "action": "bash",
        "command": "echo HEAD-MARK; for i in $(seq 1 200); do "
                   "echo filler-$i; done; echo TAIL-VERDICT",
    })
    assert result["truncated"] is True
    assert "HEAD-MARK" in result["output"]
    assert "TAIL-VERDICT" in result["output"]  # the verdict lives at the end
    assert "chars dropped" in result["output"]


def test_read_file_beyond_eof_says_so(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "small.txt").write_text("one\ntwo\n")
    result = world.execute({
        "action": "read_file", "path": "/work/small.txt", "offset": 99})
    assert result["ok"] is True
    assert result["content"] == ""
    assert "ends before offset 99" in result["note"]


def test_read_file_truncates_pathological_single_line(tmp_path):
    world = _world(tmp_path)
    (tmp_path / "work" / "oneline.txt").write_text("x" * 500_000 + "\n")
    result = world.execute({
        "action": "read_file", "path": "/work/oneline.txt"})
    assert result["ok"] is True
    assert len(result["content"]) < 2200
    assert "line truncated" in result["content"]


def test_read_file_window_only_reads_what_it_shows(tmp_path):
    world = _world(tmp_path)
    target = tmp_path / "work" / "big.log"
    target.write_text("".join(f"line-{i}\n" for i in range(1, 100_001)))
    result = world.execute({
        "action": "read_file", "path": "/work/big.log",
        "offset": 50_000, "limit": 3})
    assert result["returned_lines"] == 3
    assert "line-50000" in result["content"]
    assert "line-49999" not in result["content"]
    assert result["truncated"] is True


def test_write_file_leaves_no_partial_behind(tmp_path):
    world = _world(tmp_path)
    target = tmp_path / "work" / "sub" / "note.md"
    result = world.execute({
        "action": "write_file", "path": "/work/sub/note.md",
        "content": "hello"})
    assert result["ok"] is True
    assert target.read_text() == "hello"
    leftovers = [p.name for p in target.parent.iterdir()
                 if "partial" in p.name]
    assert leftovers == []  # the temp died with the replace
