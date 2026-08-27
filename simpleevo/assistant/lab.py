"""The seat's laboratory: one persistent writable world per lease.

The lab is a git worktree checked out at the purchased node's SHA and kept
alive for the whole lease (across attempts and adjudication reopens): the
seat's own shell edits and every ``work`` call land in the same world, and
the delivery is a snapshot of that one world (科学家完整研究制 §2.2 —
席位只有它的一问,也只有它的一个世界).

Snapshot discipline is absorbed from scripts/run_cont_agent.py's
``snapshot_commit`` (the 4h continuous-arm machinery, proven in §12): a
throwaway index stages ONLY the task's editable paths, so build artifacts,
scratch notes and edits to frozen harness files can never reach a
deliverable.  Snapshots are side commits on the node's SHA; they live in
the run clone's object store, which is exactly what lets the adjudication
worker evaluate them.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from simpleevo.contracts import SourceWorkspace, WorkspaceSpec

from .git_worktree import GitWorkspaceProvider


def _git(ws: Path, *args: str, env_extra: dict | None = None) -> str:
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.run(
        ["git", "-C", str(ws), *args], env=env,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout.strip()


def snapshot_commit(
    ws: Path, base_sha: str, paths: tuple[str, ...], tag: str,
) -> str | None:
    """Freeze the lab's editable paths into a side commit (None if unchanged).

    Uses a throwaway index so the worktree's own index/HEAD (owned by the
    seat, untouched by the harness) never moves.  Only ``paths`` are
    staged — everything else the seat or its assistant touched stays
    untracked and can never reach an adjudicated world.
    """
    if not paths:
        return None
    with tempfile.NamedTemporaryFile(prefix="lab-snapshot-idx-") as tmp:
        env = {
            "GIT_INDEX_FILE": tmp.name,
            "GIT_AUTHOR_NAME": "lab-snapshot",
            "GIT_AUTHOR_EMAIL": "lab-snapshot@harness",
            "GIT_COMMITTER_NAME": "lab-snapshot",
            "GIT_COMMITTER_EMAIL": "lab-snapshot@harness",
        }
        _git(ws, "read-tree", base_sha, env_extra=env)
        _git(ws, "add", "-A", "--", *paths, env_extra=env)
        tree = _git(ws, "write-tree", env_extra=env)
        parent_tree = _git(ws, "rev-parse", f"{base_sha}^{{tree}}")
        if tree == parent_tree:
            return None
        return _git(
            ws, "commit-tree", tree, "-p", base_sha,
            "-m", f"lab snapshot {tag}", env_extra=env,
        )


class Laboratory:
    """Persistent main world + one-shot side worlds for one lease."""

    def __init__(
        self,
        *,
        provider: GitWorkspaceProvider,
        episode_id: str,
        node_sha: str,
        editable_paths: tuple[str, ...],
    ):
        self.provider = provider
        self.episode_id = episode_id
        self.node_sha = node_sha
        self.editable_paths = tuple(editable_paths)
        self._workspace_id = f"lab-{episode_id}"

    @property
    def path(self) -> Path:
        return self.provider.wt_root / self._workspace_id

    def main(self) -> SourceWorkspace:
        """The lease's persistent world; created once, never dropped.

        ``create`` would drop a previous registration (correct for
        one-shot experiment worktrees, wrong for a persistent lab), so an
        existing directory is adopted as-is — the seat's uncommitted work
        survives attempts and reopens.
        """
        if self.path.exists():
            base = _git(self.path, "rev-parse", "HEAD")
            return SourceWorkspace(self._workspace_id, self.path, base)
        return self.provider.create(WorkspaceSpec(
            self._workspace_id, self.node_sha,
        ))

    @contextmanager
    def fresh(self, tag: str, *, base_sha: str | None = None):
        """A one-shot side world for an experiment that must not touch the
        main lab; removed on exit, its result reaches the seat only as the
        work call's distilled return."""
        ws = self.provider.create(WorkspaceSpec(
            f"lab-side-{self.episode_id}-{tag}", base_sha or self.node_sha,
        ))
        try:
            yield ws
        finally:
            self.provider.remove(ws)

    def snapshot(self, tag: str) -> str | None:
        """Side-chain snapshot of the main world's editable paths."""
        return snapshot_commit(
            self.path, self.node_sha, self.editable_paths, tag,
        )

    def changed_paths(self) -> list[str]:
        """Editable paths whose current content differs from the node."""
        if not self.path.exists():
            return []
        with tempfile.NamedTemporaryFile(prefix="lab-diff-idx-") as tmp:
            env = {"GIT_INDEX_FILE": tmp.name}
            _git(self.path, "read-tree", self.node_sha, env_extra=env)
            _git(
                self.path, "add", "-A", "--", *self.editable_paths,
                env_extra=env,
            )
            tree = _git(self.path, "write-tree", env_extra=env)
            if tree == _git(self.path, "rev-parse", f"{self.node_sha}^{{tree}}"):
                return []
            out = _git(
                self.path, "diff", "--name-only",
                f"{self.node_sha}^{{tree}}", tree,
            )
            return [line for line in out.splitlines() if line]
