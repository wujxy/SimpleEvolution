"""The world the scientist lives in: bash / read_file / write_file against
the filesystem the process already lives in.

One container = one world: there is no inner sandbox, no git, no snapshot
here — the mounts ARE the boundary. The path namespace is the container's
(``/work``, ``/repo``, ``/scratch``); ``PathBoundary`` maps it onto real
roots, so the same code runs in-container (identity mapping) and
standalone on host paths (probe/demo mode). Containment (``..``/
symlink-safe, prefix-checked) is inherited from ``PathBoundary`` /
``ResearchFiles`` unchanged.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .research_files import PathBoundary, ResearchFiles, _WRITE_MAX_CHARS

# Hard ceiling for any single bash call. Undeclared commands run on a
# short default clock (command_default_timeout_seconds) — a cheap-
# looking scan of a mounted tree must not quietly eat the hour (live
# twice: find /cvmfs, r4 and r8). An explicit per-call timeout may run
# longer — long builds, benchmark campaigns — up to this cap
# (command_timeout_seconds).
_BASH_TIMEOUT_CEILING = 1800
_BASH_TIMEOUT_DEFAULT = 300


class LocalWorld:
    """bash / read_file / write_file over the lived-in filesystem."""

    def __init__(
        self,
        *,
        work: Path,
        repo: Path,
        scratch: Path,
        timeout_seconds: int = _BASH_TIMEOUT_DEFAULT,
        cap_chars: int = 40000,
        timeout_ceiling: int | None = None,
        state: Path | None = None,
    ):
        self.work = Path(work)
        self.repo = Path(repo)
        self.scratch = Path(scratch)
        # The harness body — wire, session, memory, assistant seats.
        # The same tree as the world's .scientist by default; the
        # three-zone world container bind-mounts that tree twice
        # (read-only at /work/.scientist for actors to read their own
        # record, writable at /state for the harness's organs alone).
        # See docs/design/世界三区设计.md §3.
        self.state_dir = (
            Path(state) if state is not None
            else self.work / ".scientist"
        )
        self.boundary = PathBoundary(
            work=self.work, repo=self.repo, scratch=self.scratch,
        )
        self.files = ResearchFiles(
            work=self.work, repo=self.repo, scratch=self.scratch,
            cap_chars=cap_chars,
        )
        # timeout_seconds: the undeclared clock every bash call gets;
        # timeout_ceiling: the most a declared budget may buy.
        self.timeout_seconds = timeout_seconds
        self.timeout_ceiling = int(
            timeout_ceiling or _BASH_TIMEOUT_CEILING)
        self.cap_chars = cap_chars
        self.last_workdir = str(self.work)
        self.git_env = self._git_env()

    def _git_env(self) -> dict:
        """Git history aid, best-effort (read-only /repo). A worktree's
        ``.git`` FILE points at a host gitdir; inside the container the
        readable copy lives under the /repo mount, so translate by
        worktree name. A plain clone (``.git`` directory) needs nothing."""
        dot = self.work / ".git"
        if dot.is_dir():
            return {}
        try:
            line = dot.read_text(encoding="utf-8").strip()
        except OSError:
            return {}
        if not line.startswith("gitdir: "):
            return {}
        name = Path(line[len("gitdir: "):]).name
        if not name:
            return {}
        return {
            "GIT_DIR": f"/repo/.git/worktrees/{name}",
            "GIT_COMMON_DIR": "/repo/.git",
            "GIT_WORK_TREE": "/work",
        }

    def _normalize_path(self, path):
        """Namespace form for a tool path. Namespace input passes through;
        a real (host) path under one of the roots is rewritten into the
        namespace — in-container the two spellings coincide, standalone
        the boundaries text names the real roots so both must work."""
        if not isinstance(path, str):
            return path
        for prefix in ("/work", "/repo", "/scratch"):
            if path == prefix or path.startswith(prefix + "/"):
                return path
        try:
            candidate = Path(path).resolve()
        except OSError:
            return path
        try:
            return self.boundary.to_container(candidate)
        except ValueError:
            return path

    def execute(self, action: dict) -> dict:
        name = action["action"]
        try:
            if name == "read_file":
                return self.files.read_file(
                    self._normalize_path(action["path"]),
                    offset=action.get("offset", 1),
                    limit=action.get("limit", 400),
                )
            if name == "write_file":
                return self._write_file(action)
            if name == "bash":
                return self._bash(action)
        except KeyError as exc:
            # a malformed call (missing argument) bounces back to the
            # model as a tool error to retry — it must not kill the run
            return {"ok": False, "error": f"{name} is missing a required "
                                          f"argument: {exc}"}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"not a local action: {name}"}

    def _write_file(self, action: dict) -> dict:
        path = self._normalize_path(action["path"])
        if not (
            path == "/work" or path.startswith("/work/")
            or path == "/scratch" or path.startswith("/scratch/")
        ):
            return {
                "ok": False,
                "error": "write_file accepts paths under /work or "
                         "/scratch only",
            }
        content = action["content"]
        if not isinstance(content, str):
            return {"ok": False, "error": "content must be a string"}
        if len(content) > _WRITE_MAX_CHARS:
            return {
                "ok": False,
                "error": f"content exceeds {_WRITE_MAX_CHARS} chars",
            }
        host = self.boundary.resolve(path)
        if host.is_dir():
            return {"ok": False, "error": f"path is a directory: {path}"}
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "chars": len(content)}

    def _bash(self, action: dict) -> dict:
        command = action.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"ok": False, "error": "command must be non-empty"}
        workdir = action.get("workdir")
        if workdir is not None:
            workdir = self._normalize_path(workdir)
            if workdir == "/repo" or workdir.startswith("/repo/"):
                return {
                    "ok": False,
                    "error": "workdir must be under /work or /scratch, "
                             "not /repo",
                }
            try:
                host = self.boundary.resolve(workdir)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if not host.is_dir():
                return {
                    "ok": False, "error": f"workdir does not exist: {workdir!r}",
                }
            self.last_workdir = str(host)
        timeout = action.get("timeout_seconds") or self.timeout_seconds
        timeout = max(1, min(int(timeout), self.timeout_ceiling))
        env = dict(os.environ)
        env.update(self.git_env)
        try:
            proc = subprocess.Popen(
                ["bash", "-c", command],
                cwd=self.last_workdir,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return {"ok": False, "error": f"failed to start: {exc}"}
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            stdout, stderr = proc.communicate()
            returncode = None
        output = stdout or ""
        if stderr:
            output += "\n[stderr]\n" + stderr
        if timed_out:
            output += (
                f"\n[timeout] the command was killed at its {timeout}s "
                "budget; declare timeout_seconds on the bash call if it "
                "genuinely needs longer (ceiling "
                f"{self.timeout_ceiling}s)"
            )
        truncated = len(output) > self.cap_chars
        return {
            "ok": not timed_out and returncode == 0,
            "returncode": returncode,
            "timed_out": timed_out,
            "truncated": truncated,
            "output": output[:self.cap_chars],
        }
