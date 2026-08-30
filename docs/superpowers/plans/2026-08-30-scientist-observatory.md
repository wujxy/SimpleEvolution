# Scientist Observatory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a single-run, read-only web observer that turns Scientist and collaborator files into a truthful live timeline, deterministic activity summaries, and on-demand raw details.

**Architecture:** A Python standard-library service observes one explicit RUN_DIR. RunReader emits stable, offset-addressed file facts; RunProjector derives events and current state; Observatory retains the in-memory projection and publishes an HTTP snapshot plus SSE deltas to a dependency-free browser UI. The observer never writes to the run and never infers process liveness from PID alone.

**Tech Stack:** Python 3.9+ standard library (dataclasses, json, pathlib, threading, http.server), vanilla HTML/CSS/JavaScript, pytest. Development/test interpreter: /datafs/users/wujxy/py_venv/my_env/bin/python (currently Python 3.12.3, pytest 9.1.1).

## Global Constraints

- Observe exactly one startup-selected RUN_DIR.
- Expose no run-control operation and no POST, PUT, PATCH, or DELETE API.
- Never write into RUN_DIR; tests verify the observed tree is unchanged.
- Add no database, web framework, npm project, or LLM summarizer.
- Default bind address is 127.0.0.1; remote use is through SSH forwarding.
- Poll every one second by default and meet the two-second update bound.
- Do not fabricate timestamps. Untimed seat activity stays in its seat stream.
- Do not expose model, assistant, credentials, environment dictionaries, .env, or paths outside the selected run.
- Render run-provided strings with textContent, never innerHTML.
- Stable source event/detail IDs derive from logical source plus byte offset.
- Tests use temporary run fixtures and never modify the live omilrec run.
- Follow TDD and commit after every independently reviewable task.

---

## File Map

**Create:**

- scientist/ui/__init__.py — package marker and public exports.
- scientist/ui/__main__.py — python -m scientist.ui entry point.
- scientist/ui/reader.py — run boundary, incremental cursors, source discovery, details.
- scientist/ui/projector.py — event normalization, state projection, summaries.
- scientist/ui/server.py — Observatory state, polling, HTTP/SSE, CLI.
- scientist/ui/static/index.html — semantic page.
- scientist/ui/static/app.js — safe rendering, selection, SSE client.
- scientist/ui/static/style.css — timeline-first responsive layout.
- tests/scientist/ui/__init__.py
- tests/scientist/ui/conftest.py
- tests/scientist/ui/test_reader.py
- tests/scientist/ui/test_projector.py
- tests/scientist/ui/test_server.py

**Modify:**

- pyproject.toml — package static assets.
- .gitignore — ignore .superpowers/ visual-brainstorm artifacts.

---

### Task 1: Safe Run Boundary and Redacted Metadata

**Files:**
- Create: scientist/ui/__init__.py
- Create: scientist/ui/reader.py
- Create: tests/scientist/ui/__init__.py
- Create: tests/scientist/ui/conftest.py
- Create: tests/scientist/ui/test_reader.py

**Interfaces:**
- Produces: RunLayout.discover(run_dir: Path) -> RunLayout
- Produces: RunLayout.safe_metadata() -> dict[str, object]
- Produces: RunLayout.source_path(relative: str) -> Path
- Produces fixture: run_fixture -> tuple[Path, Path] as (run_dir, scientist_dir)

- [ ] **Step 1: Write the fixture and failing tests**

Create the fixture:

~~~python
@pytest.fixture
def run_fixture(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    scientist_dir = run_dir / "world" / ".scientist"
    (scientist_dir / "session").mkdir(parents=True)
    (run_dir / "spec.json").write_text(json.dumps({
        "goal": "make reconstruction faster",
        "episode_id": "ep-7",
        "budget": {"steps": 3000, "wall_seconds": 604800},
        "model": {"api_key": "TOP-SECRET", "base_url": "secret-host"},
        "assistant": {"env": {"ANTHROPIC_AUTH_TOKEN": "SECRET-TOKEN"}},
    }), encoding="utf-8")
    return run_dir, scientist_dir
~~~

Tests assert: a directory without world/.scientist raises ValueError; safe_metadata returns exactly goal, episode_id, and budget; rendered metadata excludes all three synthetic secrets; source_path("../private-key") raises ValueError.

- [ ] **Step 2: Verify the missing package failure**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_reader.py -q
~~~

Expected: ModuleNotFoundError for scientist.ui.

- [ ] **Step 3: Implement the boundary**

~~~python
_SPEC_KEYS = ("goal", "episode_id", "budget")

@dataclass(frozen=True)
class RunLayout:
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
        candidate = (self.run_dir / relative).resolve()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise ValueError("source path is outside selected run")
        return candidate
~~~

- [ ] **Step 4: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_reader.py -q
git add scientist/ui/__init__.py scientist/ui/reader.py tests/scientist/ui/__init__.py tests/scientist/ui/conftest.py tests/scientist/ui/test_reader.py
git commit -m "feat: define observatory run boundary"
~~~

Expected: 3 tests pass; commit contains only Task 1 files.

---

### Task 2: Incremental JSONL and Safe Detail Index

**Files:**
- Modify: scientist/ui/reader.py
- Modify: tests/scientist/ui/test_reader.py

**Interfaces:**
- Produces immutable SourceRecord(id, source, path, offset, length, raw, value).
- Produces ReaderWarning(source, message) and ReaderBatch(records, warnings, reset).
- Produces LineCursor(path: Path, source: str, json_lines: bool, max_read_bytes: int = 1048576).poll() -> ReaderBatch.
- Produces DetailIndex.register(record: SourceRecord) -> str.
- Produces DetailIndex.read(detail_id: str, max_bytes: int = 65536) -> dict[str, object].
- Produces DetailIndex.ids() -> list[str].

- [ ] **Step 1: Add failing cursor tests**

Cover these exact cases:

~~~python
def test_cursor_waits_for_torn_json_then_emits_stable_offset(tmp_path):
    path = tmp_path / "wire.jsonl"
    path.write_bytes(b'{"content":"one"}\n{"content":"two"')
    cursor = LineCursor(path, source="wire", json_lines=True)
    first = cursor.poll()
    assert [row.offset for row in first.records] == [0]
    assert first.records[0].value["content"] == "one"
    with path.open("ab") as handle:
        handle.write(b"}\n")
    second = cursor.poll()
    assert second.records[0].value["content"] == "two"
    assert second.records[0].id == f"wire:{second.records[0].offset}"

def test_detail_index_rejects_unregistered_id(tmp_path):
    path = tmp_path / "raw.txt"
    path.write_bytes(b'{"type":"assistant","text":"safe"}\n')
    record = LineCursor(
        path, source="seat:executor-1", json_lines=True).poll().records[0]
    index = DetailIndex()
    detail_id = index.register(record)
    assert index.read(detail_id)["content"]["text"] == "safe"
    with pytest.raises(KeyError):
        index.read("detail:../../private-key")
~~~

Also test truncation: poll two records, replace the file with one shorter record, and assert reset=True, one warning containing "truncated or replaced", and only the new record. Write 3,000 records with 2,000-byte payloads, set max_read_bytes to 1 MiB, and assert repeated polls make forward progress until all 3,000 records have been emitted without any poll reading more than the configured chunk plus one pending record. A final poll emits none.

- [ ] **Step 2: Verify undefined interfaces fail**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_reader.py -q
~~~

Expected: import failure for LineCursor or DetailIndex.

- [ ] **Step 3: Implement the binary cursor**

Add the dataclasses from Interfaces. LineCursor.poll follows this complete algorithm:

1. Missing file returns an empty batch.
2. If st_size is smaller than offset, clear offset and pending, set reset=True, and add one warning.
3. Open rb, seek to offset, and read at most max_read_bytes appended bytes.
4. Prefix new bytes with pending while preserving pending_offset.
5. Emit only newline-terminated lines; retain the unterminated final bytes.
6. Parse JSON only when json_lines=True. Warn and skip malformed complete JSON.
7. Use f"{source}:{absolute_offset}" as SourceRecord.id.
8. Keep no completed raw lines inside the cursor.

DetailIndex maps detail:{record.id} to an immutable locator containing the already-approved path, offset, length, source, and whether the record is JSON. It must not retain record.raw or record.value. read reopens that registered path, seeks to the registered offset, reads at most min(length, max_bytes), parses JSON only for a complete untruncated JSON record, and otherwise returns a UTF-8 replacement-decoded prefix with truncated=True. ids returns a sorted copy of registered opaque IDs. It never resolves a client path.

- [ ] **Step 4: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_reader.py -q
git add scientist/ui/reader.py tests/scientist/ui/test_reader.py
git commit -m "feat: stream observatory records by stable offsets"
~~~

Expected: all reader tests pass and polling does not reread completed data.

---

### Task 3: Discover Sources and Project Truthful State

**Files:**
- Modify: scientist/ui/reader.py
- Create: scientist/ui/projector.py
- Create: tests/scientist/ui/test_projector.py

**Interfaces:**
- Consumes: RunLayout, LineCursor, SourceRecord, ReaderBatch, DetailIndex.
- Produces: RunReader(layout: RunLayout).poll() -> ReaderBatch.
- Produces: RunProjector(metadata: dict[str, object]).
- Produces: RunProjector.apply(batch: ReaderBatch) -> list[dict[str, object]].
- Produces: RunProjector.snapshot() -> dict[str, object].

- [ ] **Step 1: Write failing recovery and seat-state tests**

Build a run.log with step 8, a model failure, supervisor resume, and resumed step 1. Create Executor manifest without digest and Searcher manifest plus done digest and read.marker. Assert:

~~~python
reader = RunReader(RunLayout.discover(run_dir))
projector = RunProjector(reader.layout.safe_metadata())
projector.apply(reader.poll())
snapshot = projector.snapshot()
assert [attempt["number"] for attempt in snapshot["attempts"]] == [1, 2]
assert snapshot["run"]["formal_status"] == "unconcluded"
assert snapshot["seats"]["executor-ep-001"]["formal_status"] == "started"
assert snapshot["seats"]["searcher-ep-002"]["formal_status"] == "done"
assert snapshot["seats"]["searcher-ep-002"]["delivered"] is True
~~~

Parametrize current conclusion outcomes deliver, abstain, cut_off, crashed. Parametrize seat statuses done, failed, timeout-salvaged, crash-salvaged. Assert a historical conclusion.*.crashed.json creates an attempt event but does not mark the current run crashed.

- [ ] **Step 2: Verify missing projector failure**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_projector.py -q
~~~

Expected: import failure for RunReader or RunProjector.

- [ ] **Step 3: Implement dynamic source discovery**

RunReader has these fixed cursors:

~~~python
FIXED_LINE_SOURCES = {
    "run-log": ("run.log", False),
    "wire": ("world/.scientist/session/wire.jsonl", True),
    "research-state": ("world/.scientist/research_state.jsonl", True),
    "research-memory": ("world/.scientist/research_memory.jsonl", True),
    "assistant-calls": ("world/.scientist/assistant_calls.jsonl", True),
    "usage": ("world/.scientist/usage.jsonl", True),
}
~~~

Each poll also discovers assistant/*/manifest.json, raw.txt, digest.json, read.marker, conclusion.json, and conclusion.*.json. JSON documents and markers emit once per (relative path, st_mtime_ns, st_size) signature. Raw sources are named seat:{collaborator-id}. Register every emitted record in RunReader.detail_index.

Poll fixed metadata, run log, manifests, digests, markers, and conclusions before seat raw cursors. Each raw cursor consumes at most its configured 1 MiB chunk per poll. RunReader exposes initial_index_complete only after every source that existed at startup has reached its then-current EOF; new appends after that do not switch the page back to initial indexing.

- [ ] **Step 4: Implement deterministic state projection**

Initial snapshot shape:

~~~python
{
    "run": {
        "metadata": metadata,
        "formal_status": "unconcluded",
        "outcome": None,
        "last_observed_at": None,
        "current_activity": "Unavailable",
        "current_judgment": None,
    },
    "attempts": [],
    "timeline": [],
    "seats": {},
    "warnings": [],
    "usage": {"calls": 0, "total_tokens": 0},
    "indexing": True,
}
~~~

Rules:

- Resume closes one attempt; the next Scientist step opens the next.
- Step number is attempt-local, never global ordering.
- Only current conclusion.json sets formal completion.
- Manifest without digest is started regardless of PID.
- Digest status maps only to allowed formal statuses.
- read.marker independently sets delivered=True.
- Source mtime sets activity hints only, never historical event time.
- Timeline contains run-log anchors, seat start/finish, research-judgment revisions, and conclusions.
- Wire tool calls and tool results appear in PI sequence with occurred_at=None unless a run-log anchor supplies a reliable time; their detail references remain expandable.
- current_activity is the last explicit run-log state such as thinking, wait, tool names, failure, resume, or conclusion; it is never inferred from silence.
- current_judgment is the latest valid research-state row, normalized from judgment or the legacy working_model field, with its evidence references retained.
- Usage sums calls and integer total_tokens fields only; absent or provider-specific fields do not invent a total.
- Deduplicate warnings by source and message.
- Same-time events sort by fixed source priority and source-local sequence.
- Snapshot indexing mirrors RunReader.initial_index_complete, so manifest state is immediately visible while large historical raw files are progressively summarized.

- [ ] **Step 5: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_reader.py tests/scientist/ui/test_projector.py -q
git add scientist/ui/reader.py scientist/ui/projector.py tests/scientist/ui/test_projector.py
git commit -m "feat: project scientist run and seat state"
~~~

Expected: all focused tests pass.

---

### Task 4: Deterministic Claude Activity Summaries

**Files:**
- Modify: scientist/ui/projector.py
- Modify: tests/scientist/ui/test_projector.py

**Interfaces:**
- Produces: summarize_seat_record(record: SourceRecord) -> dict[str, object] | None.
- Adds each seat field: activities: list[dict[str, object]].
- Activity keys: id, kind, summary, status, detail_refs, sequence.

- [ ] **Step 1: Write failing summary tests**

Use real Claude stream-json shapes. Assert a Bash tool use for bash scripts/benchmark.sh --evtmax 10 summarizes as "运行 benchmark.sh --evtmax 10". Assert Read/Edit show normalized in-run paths. Feed three tool_progress events with one tool_use_id and assert one activity contains all detail refs. Feed a digest and assert kind=report, status=collaborator_testimony, and uncertainty remains visible.

Add hostile input "<img src=x onerror=alert(1)>", a 100,000-character result, and an absolute external path. Assert summaries are capped plain strings and external paths are not exposed as detail targets.

- [ ] **Step 2: Verify summary tests fail**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_projector.py -k "summary or progress or digest" -q
~~~

Expected: failures for absent summary behavior.

- [ ] **Step 3: Implement conservative mappings**

~~~python
TOOL_LABELS = {
    "Read": "读取",
    "Grep": "搜索",
    "Glob": "查找文件",
    "Edit": "修改",
    "Write": "写入",
    "Bash": "运行",
    "WebSearch": "检索网页",
    "WebFetch": "读取网页",
    "Task": "派出子任务",
}
~~~

Rules:

- Read/Edit/Write show normalized paths only when inside the observed run.
- Grep/Glob show capped pattern and in-run root.
- Bash recognizes script basename and flags for benchmark.sh, quick_bench.sh, sl_eval_v100.sh, pytest, cmake, and make. Unknown commands use a shell-token-capped first line; never evaluate substitutions.
- Non-empty assistant text creates an intent capped at 240 display characters while retaining raw detail.
- tool_progress merges strictly by tool_use_id.
- Explicit tool results set succeeded, failed, or running.
- Digest is collaborator_testimony; preserve report_digest, evidence, uncertainty.
- Merge only records sharing a tool-use ID. Do not infer cross-tool causal stories.

- [ ] **Step 4: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_projector.py -q
git add scientist/ui/projector.py tests/scientist/ui/test_projector.py
git commit -m "feat: summarize collaborator activity deterministically"
~~~

Expected: all projector tests pass.

---

### Task 5: Read-Only HTTP, Replay, and SSE

**Files:**
- Create: scientist/ui/server.py
- Create: tests/scientist/ui/test_server.py

**Interfaces:**
- Consumes: RunReader.poll, DetailIndex.read, RunProjector.apply/snapshot.
- Produces: Observatory(layout: RunLayout, poll_seconds: float = 1.0).
- Produces: Observatory.poll_once() -> list[dict[str, object]].
- Produces: Observatory.snapshot() -> dict[str, object].
- Produces: Observatory.events_after(cursor: str | None) -> list[dict[str, object]].
- Produces: make_server(observatory, host, port) -> ThreadingHTTPServer.
- Produces: encode_sse(delta: dict[str, object]) -> bytes.

- [ ] **Step 1: Write failing HTTP tests**

Start on port 0 in a test thread. Assert GET /api/snapshot is redacted. Assert POST to every API route returns 405 with Allow: GET. Test registered detail, unknown detail 404, path-shaped detail 404, and headers Cache-Control: no-store and X-Content-Type-Options: nosniff.

- [ ] **Step 2: Verify missing server failure**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py -q
~~~

Expected: import failure for scientist.ui.server.

- [ ] **Step 3: Implement Observatory and bounded replay**

Observatory owns one reader, projector, threading.Condition, stop Event, and up to 10,000 deltas. Delta shape:

~~~python
{
    "id": "delta-42",
    "type": "event_added",
    "data": {"event_id": "wire:348123"},
}
~~~

poll_once applies one ReaderBatch and notifies subscribers. events_after returns only later deltas. A cursor older than the retained window returns snapshot_required. Source event IDs remain stable; transport delta IDs are service-lifetime cursors.

- [ ] **Step 4: Implement strict routes**

Provide only:

~~~text
GET /
GET /static/app.js
GET /static/style.css
GET /api/snapshot
GET /api/events?after=<cursor>
GET /api/details/<detail-id>
GET /api/stream
~~~

All other paths return 404. Mutating methods return 405. SSE sends id, event, JSON data, and a heartbeat comment at least every 15 seconds. BrokenPipeError and ConnectionResetError are normal disconnects.

Pure formatter assertion:

~~~python
assert encode_sse({
    "id": "delta-2", "type": "seat_updated", "data": {"x": 1},
}) == b'id: delta-2\nevent: seat_updated\ndata: {"x": 1}\n\n'
~~~

- [ ] **Step 5: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py -q
git add scientist/ui/server.py tests/scientist/ui/test_server.py
git commit -m "feat: serve observatory snapshot and live events"
~~~

Expected: server tests pass without a public port.

---

### Task 6: Timeline-First Single-Page UI

**Files:**
- Create: scientist/ui/static/index.html
- Create: scientist/ui/static/app.js
- Create: scientist/ui/static/style.css
- Modify: tests/scientist/ui/test_server.py

**Interfaces:**
- Consumes: /api/snapshot, /api/details/<detail-id>, /api/stream.
- Consumes snapshot keys: run, attempts, timeline, seats, warnings, usage, indexing.

- [ ] **Step 1: Write failing static-contract tests**

Assert HTML includes IDs run-status, timeline, seats, details. Assert app.js contains new EventSource('/api/stream') and .textContent, but not .innerHTML. Assert CSP exactly:

~~~text
default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'
~~~

- [ ] **Step 2: Verify static routes fail**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py -k frontend -q
~~~

Expected: 404 or missing-asset failures.

- [ ] **Step 3: Implement semantic layout**

Use header run status/metrics, current-state section, main timeline plus collaborator aside, and details section. Desktop grid is approximately two-thirds timeline and one-third seats; below 900 px it is one column. Status uses text and borders in addition to color. Add no charts, icon libraries, theme switcher, or forms.

Required JavaScript responsibilities and exact public function names:

~~~javascript
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
~~~

Implement renderSnapshot(value), renderTimeline(events), renderSeats(seats), showDetail(detailId), applyDelta(delta), and connectStream() using createElement and textContent exclusively. renderSnapshot assigns the received object to the single local snapshot; renderTimeline and renderSeats replace their own DOM children; showDetail URL-encodes the opaque ID before fetch; applyDelta handles each declared delta type and refetches the snapshot on snapshot_required; connectStream updates connection text on open/error and installs the declared event listeners. Keep one selected object. Auto-follow only within 48 px of the bottom; otherwise show a new-activity button. Incoming deltas never replace a user's old selection. Unknown fields render as "Unavailable".

- [ ] **Step 4: Run and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py -q
git add scientist/ui/static/index.html scientist/ui/static/app.js scientist/ui/static/style.css tests/scientist/ui/test_server.py
git commit -m "feat: add timeline-first observatory UI"
~~~

Expected: static/server tests pass and hostile HTML appears only as text.

---

### Task 7: CLI, Packaging, and Real-Run Verification

**Files:**
- Create: scientist/ui/__main__.py
- Modify: scientist/ui/server.py
- Modify: pyproject.toml
- Modify: .gitignore
- Modify: tests/scientist/ui/test_server.py

**Interfaces:**
- Produces: parse_args(argv: list[str] | None = None) -> argparse.Namespace.
- Produces: main(argv: list[str] | None = None) -> int.
- Produces command: python -m scientist.ui --run-dir DIR.

- [ ] **Step 1: Write failing CLI/package tests**

Assert defaults host=127.0.0.1, port=8765, poll_seconds=1.0. Assert zero/negative poll intervals cause SystemExit. Build and inspect a wheel without network access:

~~~python
subprocess.run([
    sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
    "--no-build-isolation", "--wheel-dir", str(tmp_path),
], check=True)
wheel = next(tmp_path.glob("simpleevo-*.whl"))
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
assert "scientist/ui/static/index.html" in names
assert "scientist/ui/static/app.js" in names
assert "scientist/ui/static/style.css" in names
~~~

- [ ] **Step 2: Verify failure**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py -k "cli or package" -q
~~~

Expected: failure for missing CLI or package data.

- [ ] **Step 3: Implement CLI and package assets**

~~~python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scientist-observatory")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--poll-seconds", default=1.0, type=positive_float)
    return parser.parse_args(argv)

def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed
~~~

main discovers the run before opening a socket, starts polling, serves until KeyboardInterrupt, then stops the observer and closes the server. Static routes load assets with importlib.resources.files("scientist.ui").joinpath("static", name), so source and installed wheels use one path. __main__.py calls raise SystemExit(main()).

Add:

~~~toml
[tool.setuptools.package-data]
"scientist.ui" = ["static/*"]
~~~

Add .superpowers/ to .gitignore without deleting or committing its existing directory.

- [ ] **Step 4: Run Observatory and Scientist regression tests**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui -q
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist -q
~~~

Expected: both suites pass.

- [ ] **Step 5: Verify a copied run is never written**

Add this integration test, using the existing run_fixture and a local helper that includes relative path, size, and nanosecond mtime:

~~~python
def tree_manifest(root: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (str(path.relative_to(root)), path.stat().st_size,
         path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    )

def test_observatory_never_writes_observed_run(run_fixture):
    run_dir, scientist = run_fixture
    (scientist / "session" / "wire.jsonl").write_text(
        '{"role":"user","content":"begin"}\n', encoding="utf-8")
    before = tree_manifest(run_dir)
    observatory = Observatory(RunLayout.discover(run_dir), poll_seconds=0.01)
    observatory.poll_once()
    observatory.snapshot()
    for detail_id in observatory.reader.detail_index.ids():
        observatory.reader.detail_index.read(detail_id)
    after = tree_manifest(run_dir)
    assert after == before
~~~

Run this test before touching the live run. The fixture manifest is the authoritative no-write proof.

- [ ] **Step 6: Inspect the live omilrec run**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m scientist.ui --run-dir runs/singlenode/omilrec-v100-r2-scientist --host 127.0.0.1 --port 8765
~~~

Through SSH forwarding, verify:

- timeline shows the interrupted and resumed attempts;
- Searcher is done and delivered;
- Executor activities are present without loading bulk raw into snapshot;
- no model key or assistant environment appears;
- missing timestamps are shown as sequence-only;
- connection loss is an observer warning, not a false run stop.

Do not use live-file mtimes alone as the no-write proof because Scientist may still be changing them; the copied fixture in Step 5 is the authoritative check.

- [ ] **Step 7: Final verification and commit**

~~~bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui tests/scientist -q
git diff --check
git status --short
git add scientist/ui/__main__.py scientist/ui/server.py pyproject.toml .gitignore tests/scientist/ui/test_server.py
git commit -m "feat: package scientist observatory CLI"
~~~

Expected: tests pass, diff check is silent, and the final commit contains only Task 7 files.

---

## Completion Gate

Before claiming completion:

1. Use superpowers:verification-before-completion.
2. Re-run /datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui tests/scientist -q from the final commit.
3. Confirm the omilrec snapshot shows recovery, concurrent seats, Searcher delivery, and Executor activity without secrets or bulk raw payloads.
4. Confirm the observer introduced no writes under the copied fixture RUN_DIR.
5. Run git status --short and report unrelated or generated files separately.
