"""Git-backed source workspace provider for experiment jobs."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from simpleevo.contracts import ChangeSet, CommitRequest, SourceWorkspace, WorkspaceError, WorkspaceSpec

# git-lfs pointer header: "oid sha256:<hex>" (the real content lives in the
# clone's .git/lfs/objects store, sha256-addressed).
_LFS_OID_RE = re.compile(rb"oid sha256:([0-9a-f]{64})")


def _lfs_objects_root(repo: Path) -> Path:
    return repo / ".git" / "lfs" / "objects"


def _materialize_lfs(tree: Path, repo: Path) -> int:
    """Replace git-lfs pointer files in ``tree`` with their real content.

    Worktrees are created with GIT_LFS_SKIP_SMUDGE=1 (nodes must not pull LFS
    content over the network), so every LFS-tracked file lands as a ~130-byte
    pointer stub.  Gates that hash repo assets (checksum manifests) would fail
    on the stub.  The real objects live in the run-dir clone's LFS store
    (populated once by ``initialize()`` from the source repo); hardlink them
    in — same filesystem, zero copy, no git-lfs binary needed on the node.
    Returns the number of files materialized.
    """
    objects = _lfs_objects_root(repo)
    if not objects.is_dir():
        return 0
    n = 0
    for path in tree.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if ".git" in path.relative_to(tree).parts:
            continue
        try:
            head = path.read_bytes()[:200]
        except OSError:
            continue
        if not head.startswith(b"version https://git-lfs"):
            continue
        match = _LFS_OID_RE.search(head)
        if not match:
            continue
        oid = match.group(1).decode()
        src = objects / oid[:2] / oid[2:4] / oid
        if not src.is_file():
            raise WorkspaceError(
                f"LFS object {oid[:12]}… missing from run-dir clone store "
                f"({path.relative_to(tree)}); run `git lfs fetch` in "
                f"{repo} or re-init the run dir"
            )
        path.unlink()
        try:
            os.link(src, path)
        except OSError:
            shutil.copy2(src, path)
        n += 1
    return n


class GitWorkspaceProvider:
    """Create worktrees from a parent SHA, inspect changes, and commit on behalf
    of the harness.  This is a trimmed-down port of SimpleLoop's world/git.py
    with round/candidate/lane identifiers replaced by experiment identity.
    """

    def __init__(
        self,
        run_dir: str | Path,
        repo_path: str | Path,
    ):
        # Resolve to absolute paths up front: ``git -C <repo> worktree add
        # <path>`` interprets a relative ``path`` against the ``-C`` directory,
        # not the caller's cwd, so a relative run_dir would create worktrees
        # inside the repo clone (and then fail to find them on read-back).
        self.run_dir = Path(run_dir).resolve()
        self.source_repo = Path(repo_path).resolve()
        self.repo = self.run_dir / "repo"
        self.wt_root = self.run_dir / "worktrees"

    def initialize(self) -> str:
        if not self.repo.exists():
            self.run_dir.mkdir(parents=True, exist_ok=True)
            env = {
                **os.environ,
                "GIT_LFS_SKIP_SMUDGE": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
            self._clone(env)
            self._seed_lfs_store()
        return self._git(self.repo, "rev-parse", "HEAD")

    def _seed_lfs_store(self) -> None:
        """Populate the clone's LFS object store from the source repo.

        A plain git clone does not carry .git/lfs/objects, and nodes must not
        fetch LFS content over the network.  The source repo's store is on the
        same filesystem, so hardlink each object in (zero copy).
        """
        src = _lfs_objects_root(self.source_repo)
        if not src.is_dir():
            return
        dst = _lfs_objects_root(self.repo)
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            target = dst / rel
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, target)
            except OSError:
                shutil.copy2(path, target)

    def _clone(self, env: dict[str, str]) -> None:
        completed = None
        for args in (
            ["git", "clone", "--local", "--no-checkout", str(self.source_repo), str(self.repo)],
            ["git", "clone", "--no-checkout", str(self.source_repo), str(self.repo)],
        ):
            completed = subprocess.run(
                args,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            if completed.returncode == 0:
                return
        detail = completed.stderr.strip() if completed else "unknown error"
        raise WorkspaceError(
            f"git clone failed: {detail}\n"
            "(both --local hardlink and full copy failed)"
        )

    def create(self, spec: WorkspaceSpec) -> SourceWorkspace:
        self._validate_id(spec.workspace_id)
        self.wt_root.mkdir(parents=True, exist_ok=True)
        path = self.wt_root / spec.workspace_id
        branch = f"simpleevo/{spec.workspace_id}"
        self._drop_stale(path)
        env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
        completed = subprocess.run(
            [
                "git", "-C", str(self.repo), "worktree", "add",
                "-B", branch, str(path), spec.revision,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        if completed.returncode:
            raise WorkspaceError(
                f"git worktree add failed: {completed.stderr.strip()}"
            )
        base_sha = self._git(path, "rev-parse", "HEAD")
        _materialize_lfs(path, self.repo)
        return SourceWorkspace(spec.workspace_id, path, base_sha)

    @contextmanager
    def open(self, spec: WorkspaceSpec):
        workspace = self.create(spec)
        try:
            yield workspace
        finally:
            self.remove(workspace)

    def remove(self, workspace: SourceWorkspace) -> None:
        self._remove_path(workspace.path)

    def reset(self, workspace: SourceWorkspace) -> None:
        """Return a workspace to its pristine base revision."""
        if not workspace.path.exists():
            return
        self._git(workspace.path, "reset", "--hard", workspace.base_sha or "HEAD")
        self._git(workspace.path, "clean", "-fd")
        # reset --hard rewrites LFS-tracked files back to pointer stubs.
        _materialize_lfs(workspace.path, self.repo)

    def inspect(self, workspace: SourceWorkspace) -> ChangeSet:
        completed = subprocess.run(
            [
                "git", "-C", str(workspace.path), "status",
                "--porcelain=v1", "-z", "--untracked-files=all",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise WorkspaceError(
                "git status --porcelain=v1 failed: "
                f"{completed.stderr.strip()}"
            )
        return ChangeSet(tuple(
            PurePosixPath(p) for p in _status_paths(completed.stdout)
        ))

    def commit(
        self,
        workspace: SourceWorkspace,
        request: CommitRequest,
    ) -> str:
        paths = [path.as_posix() for path in request.changed_paths]
        self._git(workspace.path, "restore", "--staged", "--", ".")
        self._git(workspace.path, "add", "--", *paths)
        self._git(
            workspace.path,
            "-c", "user.name=SimpleEvolution",
            "-c", "user.email=evo@example.invalid",
            "commit", "-m",
            f"SimpleEvolution experiment {request.experiment_id}\n"
            f"proposal {request.proposal_id}",
        )
        sha = self._git(workspace.path, "rev-parse", "HEAD")
        remaining = self.inspect(workspace).paths
        if remaining:
            raise WorkspaceError(
                "worktree differs from committed result: "
                + ", ".join(path.as_posix() for path in remaining)
            )
        return sha

    def _drop_stale(self, path: Path) -> None:
        """Clear any previous registration for this workspace id.

        A retried/re-submitted worker can race another live worker on the
        same id: the directory may exist, or git may hold a stale worktree
        registration / branch from an attempt whose directory was already
        wiped (``worktree add -B`` then dies with "already exists" /
        "is missing but its branch..."). Remove all three layers — the
        git registration first (it refuses directory removal otherwise),
        then the branch, then whatever directory remains.
        """
        subprocess.run(
            [
                "git", "-C", str(self.repo), "worktree", "remove",
                "--force", str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repo), "worktree", "prune",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if path.exists():
            self._remove_path(path)

    def _remove_path(self, path: Path) -> None:
        resolved = path.resolve()
        root = self.wt_root.resolve()
        if not (resolved == root or root in resolved.parents):
            raise WorkspaceError(
                f"workspace path is outside provider roots: {path}"
            )
        if not path.exists():
            return
        subprocess.run(
            [
                "git", "-C", str(self.repo), "worktree", "remove",
                "--force", str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "prune"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # A directory with no git registration (worker died before
        # `worktree add` registered it, or registration lived under a
        # different path) survives both git commands — remove it directly.
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _validate_id(workspace_id: str) -> None:
        if (
            not workspace_id
            or workspace_id in {".", ".."}
            or "/" in workspace_id
            or "\\" in workspace_id
        ):
            raise WorkspaceError(f"invalid workspace id: {workspace_id!r}")

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise WorkspaceError(
                f"git {' '.join(args)} failed: {completed.stderr.strip()}"
            )
        return completed.stdout.strip()


def _status_paths(status: str) -> list[str]:
    """Parse NUL-delimited porcelain v1, retaining both rename paths."""
    fields = status.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        code = field[:2]
        path = field[3:] if len(field) > 3 else ""
        if path and path not in paths:
            paths.append(path)
        if "R" in code or "C" in code:
            if index < len(fields):
                source = fields[index]
                index += 1
                if source and source not in paths:
                    paths.append(source)
    return paths
