"""Safe, incremental reads from one selected Scientist run."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


_SPEC_KEYS = ("goal", "episode_id", "budget")


@dataclass(frozen=True)
class RunLayout:
    """Resolved filesystem boundary for one run."""

    run_dir: Path
    scientist_dir: Path

    @classmethod
    def discover(cls, run_dir: Path) -> "RunLayout":
        root = Path(run_dir).resolve()
        scientist = root / "world" / ".scientist"
        if not root.is_dir() or not scientist.is_dir():
            raise ValueError(
                f"RUN_DIR must contain readable world/.scientist: {root}")
        return cls(root, scientist)

    def safe_metadata(self) -> dict[str, object]:
        """Return only display-safe spec fields."""
        path = self.run_dir / "spec.json"
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {key: loaded[key] for key in _SPEC_KEYS if key in loaded}

    def source_path(self, relative: str) -> Path:
        """Resolve a source path without allowing escape from this run."""
        candidate = (self.run_dir / relative).resolve()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise ValueError("source path is outside selected run")
        return candidate


@dataclass(frozen=True)
class SourceRecord:
    """One complete source line and its stable byte location."""

    id: str
    source: str
    path: Path
    offset: int
    length: int
    raw: bytes
    value: object
    is_json: bool


@dataclass(frozen=True)
class ReaderWarning:
    source: str
    message: str


@dataclass(frozen=True)
class ReaderBatch:
    records: list[SourceRecord]
    warnings: list[ReaderWarning]
    reset: bool = False


class LineCursor:
    """Incrementally read complete newline-terminated records."""

    def __init__(
        self,
        path: Path,
        *,
        source: str,
        json_lines: bool,
        max_read_bytes: int = 1024 * 1024,
    ):
        if max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be positive")
        self.path = Path(path)
        self.source = source
        self.json_lines = json_lines
        self.max_read_bytes = max_read_bytes
        self.offset = 0
        self.pending = b""
        self.pending_offset = 0
        self.last_read_bytes = 0
        self._identity: tuple[int, int] | None = None

    def poll(self) -> ReaderBatch:
        warnings: list[ReaderWarning] = []
        reset = False
        self.last_read_bytes = 0
        try:
            stat = self.path.stat()
        except OSError:
            return ReaderBatch([], [])

        identity = (stat.st_dev, stat.st_ino)
        if (self._identity is not None and identity != self._identity
                or stat.st_size < self.offset):
            self.offset = 0
            self.pending = b""
            self.pending_offset = 0
            reset = True
            warnings.append(ReaderWarning(
                self.source, "source was truncated or replaced; rebuilt"))
        self._identity = identity

        read_offset = self.offset
        try:
            with self.path.open("rb") as handle:
                handle.seek(read_offset)
                chunk = handle.read(self.max_read_bytes)
        except OSError as exc:
            warnings.append(ReaderWarning(
                self.source, f"source read failed: {exc}"))
            return ReaderBatch([], warnings, reset)
        self.last_read_bytes = len(chunk)
        self.offset += len(chunk)
        if not chunk:
            return ReaderBatch([], warnings, reset)

        data_offset = self.pending_offset if self.pending else read_offset
        data = self.pending + chunk
        parts = data.split(b"\n")
        if data.endswith(b"\n"):
            complete, self.pending = parts[:-1], b""
            self.pending_offset = self.offset
        else:
            complete, self.pending = parts[:-1], parts[-1]
            self.pending_offset = (
                data_offset + sum(len(item) + 1 for item in complete))

        records: list[SourceRecord] = []
        record_offset = data_offset
        for line in complete:
            raw = line + b"\n"
            length = len(raw)
            if self.json_lines:
                try:
                    value: object = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    warnings.append(ReaderWarning(
                        self.source,
                        f"malformed complete JSON at {record_offset}: {exc}",
                    ))
                    record_offset += length
                    continue
            else:
                value = line.decode("utf-8", errors="replace")
            records.append(SourceRecord(
                id=f"{self.source}:{record_offset}",
                source=self.source,
                path=self.path,
                offset=record_offset,
                length=length,
                raw=raw,
                value=value,
                is_json=self.json_lines,
            ))
            record_offset += length
        return ReaderBatch(records, warnings, reset)


@dataclass(frozen=True)
class _DetailLocator:
    path: Path
    source: str
    offset: int
    length: int
    is_json: bool


class DetailIndex:
    """Opaque detail IDs mapped only to records observed by RunReader."""

    def __init__(self):
        self._locators: dict[str, _DetailLocator] = {}

    def register(self, record: SourceRecord) -> str:
        detail_id = f"detail:{record.id}"
        self._locators[detail_id] = _DetailLocator(
            path=record.path,
            source=record.source,
            offset=record.offset,
            length=record.length,
            is_json=record.is_json,
        )
        return detail_id

    def ids(self) -> list[str]:
        return sorted(self._locators)

    def read(
        self,
        detail_id: str,
        max_bytes: int = 65536,
    ) -> dict[str, object]:
        locator = self._locators[detail_id]
        limit = min(locator.length, max_bytes)
        with locator.path.open("rb") as handle:
            handle.seek(locator.offset)
            raw = handle.read(limit)
        truncated = len(raw) < locator.length
        if locator.is_json and not truncated:
            try:
                content: object = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                content = raw.decode("utf-8", errors="replace")
        else:
            content = raw.decode("utf-8", errors="replace")
        return {
            "detail_id": detail_id,
            "source": locator.source,
            "content": content,
            "truncated": truncated,
        }
