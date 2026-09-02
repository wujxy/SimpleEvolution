"""Host-side read-only navigation + scratch writes for the research tools.

The agent speaks one path namespace — the container paths ``/work``,
``/repo``, ``/scratch`` — and this module maps them onto the host roots
those container paths are bind-mounted from. Reads never enter the
container: the bind mounts expose the same bytes on the host, so
navigation (the bulk of research traffic) runs at host speed. Every
resolved path is verified to stay under its root (symlink- and
``..``-safe) before any I/O, and every result is rendered back in the
container namespace so host paths never leak into later shell commands.

Usage errors raise ``ValueError``; the world converts them to
``{"ok": False, "error": ...}`` observations.
"""
from __future__ import annotations

from pathlib import Path

_READ_DEFAULT_LIMIT = 400
_READ_MAX_LIMIT = 2000
_READ_LINE_CHAR_CAP = 2000
_WRITE_MAX_CHARS = 100_000


class PathBoundary:
    """Map container paths onto host roots with containment enforcement."""

    def __init__(self, *, work: Path, repo: Path, scratch: Path):
        self.roots = {
            "/work": Path(work).resolve(),
            "/repo": Path(repo).resolve(),
            "/scratch": Path(scratch).resolve(),
        }

    def resolve(self, container_path: str, *, only: str | None = None) -> Path:
        """Resolve a container path to a host path under its root.

        ``only`` restricts the allowed prefix (e.g. ``"/scratch"`` for
        writes). Raises ``ValueError`` on non-strings, unknown or
        disallowed prefixes, relative paths, and ``..``/symlink escapes.
        """
        if not isinstance(container_path, str) or not container_path:
            raise ValueError("path must be a non-empty string")
        for prefix, root in self.roots.items():
            if container_path == prefix or container_path.startswith(
                prefix + "/"
            ):
                if only is not None and prefix != only:
                    raise ValueError(
                        f"path must be under {only}: {container_path!r}"
                    )
                relative = container_path[len(prefix):].lstrip("/")
                candidate = (root / relative).resolve()
                if candidate != root and root not in candidate.parents:
                    raise ValueError(
                        f"path escapes {prefix}: {container_path!r}"
                    )
                return candidate
        raise ValueError(
            "path must be absolute under /work, /repo, or /scratch: "
            f"{container_path!r}"
        )

    def to_container(self, host_path: Path) -> str:
        """Render a host path back in the agent's container namespace."""
        host_path = Path(host_path)
        for prefix, root in self.roots.items():
            if host_path == root:
                return prefix
            if root in host_path.parents:
                return prefix + "/" + host_path.relative_to(root).as_posix()
        raise ValueError(f"host path outside all roots: {host_path}")


class ResearchFiles:
    """Host-side file operations backing the navigation research tools."""

    def __init__(
        self,
        *,
        work: Path,
        repo: Path,
        scratch: Path,
        cap_chars: int,
    ):
        self.boundary = PathBoundary(work=work, repo=repo, scratch=scratch)
        self.cap_chars = int(cap_chars)

    # -- read_file ----------------------------------------------------

    def read_file(
        self, path: str, *, offset: int = 1, limit: int = _READ_DEFAULT_LIMIT,
    ) -> dict:
        offset = _as_int(offset, "offset", minimum=1)
        limit = _as_int(limit, "limit", minimum=1)
        limit = min(limit, _READ_MAX_LIMIT)
        host = self.boundary.resolve(path)
        if not host.exists():
            raise ValueError(f"file does not exist: {path!r}")
        if not host.is_file():
            raise ValueError(f"not a regular file: {path!r}")
        # stream only the window: the whole-file load this replaces read
        # (and list-ified) gigabytes to serve a 400-line window — the
        # TEMP eval logs are the everyday multi-MB case
        window: list[str] = []
        more_after = False
        with host.open("r", encoding="utf-8", errors="replace") as handle:
            for lineno, line in enumerate(handle, start=1):
                if lineno < offset:
                    continue
                if len(window) >= limit:
                    more_after = True
                    break
                window.append(line.rstrip("\n"))
        rendered = "\n".join(
            f"{n:>6}\t{self._capped_line(text)}"
            for n, text in enumerate(window, start=offset)
        )
        truncated = more_after
        if len(rendered) > self.cap_chars:
            rendered = rendered[: self.cap_chars]
            truncated = True
        result = {
            "ok": True,
            "path": path,
            "offset": offset,
            "returned_lines": len(window),
            "truncated": truncated,
            "content": rendered,
        }
        if not window and offset > 1:
            result["note"] = (
                f"empty window — the file ends before offset {offset}"
            )
        return result

    @staticmethod
    def _capped_line(text: str) -> str:
        """A pathological single line must not render unbounded."""
        if len(text) <= _READ_LINE_CHAR_CAP:
            return text
        return text[:_READ_LINE_CHAR_CAP] + " …[line truncated]"


def _as_int(value, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value
