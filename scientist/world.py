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

import fnmatch
import os
import re
import signal
import subprocess
import time
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

# The bounded searchers (find_files / search_text): the economics are
# the tool, not the caller's discipline. A whole-tree scan of a mounted
# filesystem (find /cvmfs — twice live, r4 and r8) dies at the budget
# with an honest report of what was covered, matches cap out, big and
# binary files are skipped before they are read. Any tree, any root,
# always cheap.
_SEARCH_DEADLINE_S = 15.0
_SEARCH_MAX_RESULTS = 200
_SEARCH_MATCHES_PER_FILE = 50
_SEARCH_FILE_BYTE_CAP = 2_000_000

# Concurrent background bash jobs: the PI's own audits run detached
# while it keeps thinking; the cap keeps a forgotten job from piling
# up behind the attention that must eventually read them.
_BACKGROUND_JOBS_MAX = 4


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
        search_budget_seconds: float = _SEARCH_DEADLINE_S,
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
        self.search_budget_seconds = float(search_budget_seconds)
        # background bash registry: job_id -> {proc, log, command, ...}
        self._jobs: dict[str, dict] = {}
        self._job_counter = 0
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
            if name == "find_files":
                return self._find_files(action)
            if name == "search_text":
                return self._search_text(action)
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
        # atomic replace: a write that dies mid-way must leave either
        # the old file or none — never a half-written source in a tree
        # whose git record IS the lab notebook
        tmp = host.with_name(f".{host.name}.partial-write")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, host)
        finally:
            tmp.unlink(missing_ok=True)
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
        if action.get("background"):
            return self._bash_background(command, timeout)
        env = self._bash_env()
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
        if truncated:
            output = self._fit_cap(output)
        return {
            "ok": not timed_out and returncode == 0,
            "returncode": returncode,
            "timed_out": timed_out,
            "truncated": truncated,
            # head+tail rebuild above already fits the cap; slicing here
            # again would cut off exactly the tail it just preserved
            "output": output,
        }

    def _fit_cap(self, output: str) -> str:
        """Head and tail both survive: the verdict of a build, gate
        suite, or eval lives at the END (EVAL_RESULT=ok, the compile
        error, the pytest summary) — head-only truncation kept the
        preamble and dropped exactly what is acted on."""
        head = self.cap_chars // 2
        dropped = len(output) - self.cap_chars
        return (
            output[:head]
            + f"\n[... {dropped} chars dropped — head and tail "
              "kept ...]\n"
            + output[-(self.cap_chars - head):]
        )

    def _bash_env(self) -> dict:
        env = dict(os.environ)
        env.update(self.git_env)
        return env

    # -- background bash ------------------------------------------------
    # A long PI-run audit (the full eval, a from-zero build) no longer
    # freezes cognition: spawn, hand back a handle, keep thinking. The
    # finished result arrives as its own observation at the next turn
    # boundary — the same mailbox seat completions ride.

    def _bash_background(self, command: str, timeout: int) -> dict:
        if len(self._jobs) >= _BACKGROUND_JOBS_MAX:
            running = ", ".join(sorted(self._jobs))
            return {
                "ok": False,
                "error": f"{_BACKGROUND_JOBS_MAX} background jobs are "
                         f"already running ({running}); let one finish "
                         "first",
            }
        self._job_counter += 1
        job_id = f"bashjob-{self._job_counter:03d}"
        log = self.scratch / "jobs" / f"{job_id}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log.open("wb") as sink:
                proc = subprocess.Popen(
                    ["bash", "-c", command],
                    cwd=self.last_workdir,
                    env=self._bash_env(),
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            return {"ok": False, "error": f"failed to start: {exc}"}
        self._jobs[job_id] = {
            "proc": proc, "log": log, "command": command,
            "timeout": timeout,
            "deadline": time.monotonic() + timeout,
        }
        return {
            "ok": True, "background": True, "job_id": job_id,
            "timeout_seconds": timeout,
            "note": "running detached — the finished result (output "
                    "head+tail, exit code) arrives as its own "
                    "observation at your next turn boundary",
        }

    def poll_bash_jobs(self) -> list[dict]:
        """Finished background jobs, exactly once each; a job past its
        budget is killed and reported timed out."""
        results: list[dict] = []
        for job_id, job in list(self._jobs.items()):
            proc = job["proc"]
            rc = proc.poll()
            timed_out = False
            if rc is None:
                if time.monotonic() < job["deadline"]:
                    continue
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                rc = proc.returncode
            try:
                output = job["log"].read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            truncated = len(output) > self.cap_chars
            if truncated:
                output = self._fit_cap(output)
            if timed_out:
                output += (
                    f"\n[timeout] the background command was killed at "
                    f"its {job['timeout']}s budget; declare "
                    "timeout_seconds if it genuinely needs longer "
                    f"(ceiling {self.timeout_ceiling}s)"
                )
            results.append({
                "ok": (not timed_out) and rc == 0,
                "job_id": job_id,
                "returncode": rc,
                "timed_out": timed_out,
                "truncated": truncated,
                "command": job["command"][:200],
                "output": output,
            })
            del self._jobs[job_id]
        return results

    # -- bounded searchers ---------------------------------------------
    # The economics are the tool, not the caller's discipline: a scan
    # that outgrows its budget stops and says what it covered, matches
    # cap out, big and binary files are skipped before they are read.
    # Any tree — including a mounted one — is therefore always cheap to
    # ask (the live failure this replaces: find /cvmfs, r4 and r8).

    def _search_root(self, action: dict) -> tuple[Path, bool]:
        """Resolve the search root: inside the boundary via the mapping,
        outside it (a read-only mount) as itself."""
        root = action.get("root") or "/work"
        if not isinstance(root, str) or not root.startswith("/"):
            raise ValueError(f"root must be absolute: {root!r}")
        try:
            resolved = self.boundary.resolve(root)
        except ValueError:
            pass  # outside work/repo/scratch — a mounted tree
        else:
            if not resolved.is_dir():
                raise ValueError(f"root is not a directory: {root!r}")
            return resolved, True
        candidate = Path(root)
        if not candidate.is_dir():
            raise ValueError(f"root does not exist: {root!r}")
        return candidate, False

    def _walk_bounded(self, root: Path, deadline: float,
                      state: dict):
        """Yield (path, size) under root until the tree ends or the
        wall-clock budget does. state['exhausted'] is True only on
        natural completion; state['scanned'] counts entries seen."""
        state["exhausted"] = False
        stack = [root]
        while stack:
            if time.monotonic() >= deadline:
                return
            d = stack.pop()
            try:
                with os.scandir(d) as scan:
                    for entry in scan:
                        state["scanned"] += 1
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            size = entry.stat(
                                follow_symlinks=False).st_size
                        except OSError:
                            continue
                        yield Path(entry.path), size
            except OSError:
                continue
        state["exhausted"] = True

    def _search(self, action: dict, match) -> dict:
        host_root, mapped = self._search_root(action)
        deadline = time.monotonic() + self.search_budget_seconds
        state: dict = {"exhausted": False, "scanned": 0}
        matches: list[str] = []
        for entry, size in self._walk_bounded(
                host_root, deadline, state):
            container = (self.boundary.to_container(entry) if mapped
                         else str(entry))
            relative = entry.relative_to(host_root).as_posix()
            found = match(entry, size, container, relative)
            if found:
                matches.extend(found)
                if len(matches) >= _SEARCH_MAX_RESULTS:
                    break
        report = {
            "ok": True,
            "matches": matches[:_SEARCH_MAX_RESULTS],
            "scanned_entries": state["scanned"],
            "budget_exhausted": not state["exhausted"],
        }
        if len(matches) >= _SEARCH_MAX_RESULTS:
            report["note"] = (
                "result cap reached — more may exist; narrow the "
                "pattern or root for a precise answer"
            )
        elif not state["exhausted"]:
            report["note"] = (
                "the scan budget ran out before the tree ended — "
                "narrow the root or pattern; silence elsewhere means "
                "unscanned, not absent"
            )
        return report

    def _find_files(self, action: dict) -> dict:
        pattern = action.get("pattern")
        if not pattern:
            return {"ok": False, "error": "pattern is required"}

        def match(entry, size, container, relative):
            return [container] if (
                fnmatch.fnmatch(entry.name, pattern)
                or fnmatch.fnmatch(relative, pattern)) else None

        return self._search(action, match)

    def _search_text(self, action: dict) -> dict:
        try:
            regex = re.compile(action.get("pattern") or "")
        except re.error as exc:
            return {"ok": False, "error": f"bad pattern: {exc}"}
        glob_filter = action.get("glob")

        def match(entry, size, container, relative):
            if size is not None and size > _SEARCH_FILE_BYTE_CAP:
                return None
            if glob_filter and not fnmatch.fnmatch(
                    entry.name, glob_filter):
                return None
            try:
                with open(entry, "rb") as handle:
                    head = handle.read(8192)
                    if b"\x00" in head:
                        return None  # binary
                    body = head + handle.read()
            except OSError:
                return None
            hits = []
            for lineno, line in enumerate(
                    body.decode("utf-8", errors="replace").splitlines(),
                    start=1):
                if regex.search(line):
                    hits.append(f"{container}:{lineno}:"
                                f"{line.strip()[:200]}")
                    if len(hits) >= _SEARCH_MATCHES_PER_FILE:
                        break
            return hits or None

        return self._search(action, match)
