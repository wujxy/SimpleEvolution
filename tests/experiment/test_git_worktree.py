"""Tests for the git worktree provider."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from experiment.contracts import CommitRequest, WorkspaceSpec
from experiment.git_worktree import GitWorkspaceProvider


def _run(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_create_worktree_and_commit():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source = tmp / "source"
        source.mkdir()
        _run(source, "init")
        _run(source, "config", "user.email", "test@example.invalid")
        _run(source, "config", "user.name", "Test")
        (source / "README.md").write_text("hello")
        _run(source, "add", "README.md")
        _run(source, "commit", "-m", "initial")

        base_sha = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
        ).strip()

        run_dir = tmp / "run"
        provider = GitWorkspaceProvider(run_dir, source)
        provider.initialize()
        workspace = provider.create(WorkspaceSpec("exp-1", base_sha))

        assert workspace.base_sha == base_sha
        assert (workspace.path / "README.md").exists()

        (workspace.path / "NEW.txt").write_text("world")
        sha = provider.commit(workspace, CommitRequest(
            experiment_id="exp-1",
            proposal_id="prop-1",
            parent_sha=base_sha,
            changed_paths=(Path("NEW.txt"),),
        ))

        assert sha != base_sha
        diff = subprocess.check_output(
            ["git", "-C", str(run_dir / "repo"), "diff", "--name-only", f"{base_sha}..{sha}"],
            text=True,
        ).strip()
        assert diff == "NEW.txt"

        provider.remove(workspace)
