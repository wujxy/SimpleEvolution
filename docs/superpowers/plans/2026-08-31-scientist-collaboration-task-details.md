# Scientist Collaboration Task Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the exact `brief` and, when supplied, `definition_of_done` for Scientist collaborator calls in Observatory activity details.

**Architecture:** Keep `RunReader` and the Scientist runtime unchanged. Extend `RunProjector` to turn each supported collaborator tool call in a wire record into its own deterministic `collaboration_task` event containing only whitelisted task fields; extend the vanilla JavaScript renderer to show those fields as plain text while retaining the existing raw-record view.

**Tech Stack:** Python 3.9+ standard library, vanilla JavaScript/DOM, pytest. Test interpreter: `/datafs/users/wujxy/py_venv/my_env/bin/python`.

## Global Constraints

- Read only `brief` and `definition_of_done` from collaborator tool arguments.
- Do not read `prompt.txt` or change Scientist runtime, wire, manifest, or reader contracts.
- Recognize `executor`, `searcher`, `proposer`, `challenger`, `reviewer`, and `continue_engagement`.
- Render every task field with `textContent`; never interpret HTML or Markdown.
- Preserve the existing opaque raw `detail_ref` and read-only security boundary.
- Keep full task fields in activity details; cap only the timeline summary.
- One malformed call must not suppress valid sibling calls in the same wire record.
- Add no dependency, endpoint, database, or write operation.
- Preserve unrelated worktree changes and commit only task files.

---

## File Map

**Modify:**

- `scientist/ui/projector.py` — parse supported collaboration calls and emit one stable structured event per call.
- `scientist/ui/static/app.js` — render semantic task details and switch to the existing raw record view.
- `scientist/ui/static/style.css` — minimally style task-detail labels, text blocks, and actions.
- `tests/scientist/ui/test_projector.py` — cover parsing, stable IDs, sibling calls, and degradation.
- `tests/scientist/ui/test_server.py` — assert the frontend semantic-detail and safe-rendering contract.

No files are created by the implementation.

---

### Task 1: Project Collaborator Calls as Structured Activities

**Files:**

- Modify: `scientist/ui/projector.py`
- Test: `tests/scientist/ui/test_projector.py`

**Interfaces:**

- Add private `_collaboration_events(record: SourceRecord) -> list[dict[str, object]]`.
- Each returned event has `id`, `kind="collaboration_task"`, `role`, `summary`, `task`, `occurred_at`, `sequence`, `detail_refs`, and `_sort_key`.
- `task` contains `brief`, optional `definition_of_done`, and `available`.
- `RunProjector._record()` appends every returned event through the existing event-ID and timeline machinery and emits one `event_added` delta per added event.

- [ ] **Step 1: Write failing projector tests for valid collaboration calls**

Add a `_wire_record` helper and these assertions:

```python
def _wire_record(tmp_path: Path, value: object, offset: int = 40):
    raw = (json.dumps(value) + "\n").encode()
    return SourceRecord(
        id=f"wire:{offset}", source="wire", path=tmp_path / "wire.jsonl",
        offset=offset, length=len(raw), raw=raw, value=value, is_json=True,
    )

def test_wire_projects_each_collaboration_call_with_exact_task(tmp_path):
    record = _wire_record(tmp_path, {
        "role": "assistant",
        "tool_calls": [
            {"id": "call-exec", "function": {
                "name": "executor",
                "arguments": json.dumps({
                    "brief": "profile the hot loop\nwithout changing behavior",
                    "definition_of_done": "report stage timings",
                    "workspace": "isolated",
                }),
            }},
            {"id": "call-search", "function": {
                "name": "searcher",
                "arguments": {"brief": "inspect external-vertex consumers"},
            }},
        ],
    })
    projector = RunProjector({})
    projector.apply(ReaderBatch([record], [], initial_index_complete=True))

    events = [event for event in projector.snapshot()["timeline"]
              if event["kind"] == "collaboration_task"]
    assert [event["id"] for event in events] == [
        "wire:40:call-exec", "wire:40:call-search"]
    assert events[0]["task"] == {
        "brief": "profile the hot loop\nwithout changing behavior",
        "definition_of_done": "report stage timings",
        "available": True,
    }
    assert events[1]["task"] == {
        "brief": "inspect external-vertex consumers",
        "available": True,
    }
    assert events[0]["detail_refs"] == ["detail:wire:40"]
```

Also assert `workspace` is absent, the summary contains the role plus the capped first brief paragraph, and an event without `tool_call.id` receives stable suffix `index-0`.

- [ ] **Step 2: Run the valid-call tests and verify failure**

```bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_projector.py -q
```

Expected: the new tests fail because wire records still produce one generic `wire` event without `task`.

- [ ] **Step 3: Implement the minimal collaboration-call projector**

Add constants and argument decoding:

```python
import json

_COLLABORATION_TOOLS = {
    "executor", "searcher", "proposer", "challenger", "reviewer",
    "continue_engagement",
}

def _collaboration_arguments(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None
```

Implement `_collaboration_events` by iterating all tool calls, ignoring unsupported names, decoding each call independently, retaining only string `brief` and string `definition_of_done`, and setting `available` only when `brief` is a non-empty string. Use the record ID plus call ID, or `index-{index}` fallback, for the event ID. Use `_cap(brief.split("\n\n", 1)[0])` for the short summary and retain the complete strings in `task`.

Refactor `_event` only as far as necessary to accept optional `role` and `task` keyword data while preserving all current callers and sorting. In the wire branch, emit structured events for supported calls and retain the existing generic wire event only when the record has no supported collaboration call.

- [ ] **Step 4: Add failing degradation and replay tests**

Add tests covering:

```python
@pytest.mark.parametrize("arguments", ["{broken", [], 7, None])
def test_collaboration_call_with_bad_arguments_degrades_truthfully(
        tmp_path, arguments):
    record = _wire_record(tmp_path, {
        "role": "assistant",
        "tool_calls": [{"id": "bad", "function": {
            "name": "executor", "arguments": arguments,
        }}],
    })
    projector = RunProjector({})
    projector.apply(ReaderBatch([record], [], initial_index_complete=True))
    event = projector.snapshot()["timeline"][0]
    assert event["kind"] == "collaboration_task"
    assert event["task"] == {"available": False}
    assert event["detail_refs"] == ["detail:wire:40"]

def test_bad_collaboration_call_does_not_hide_valid_sibling(tmp_path):
    record = _wire_record(tmp_path, {
        "role": "assistant",
        "tool_calls": [
            {"id": "bad", "function": {
                "name": "executor", "arguments": "{broken",
            }},
            {"id": "good", "function": {
                "name": "reviewer",
                "arguments": json.dumps({"brief": "audit the evidence"}),
            }},
        ],
    })
    projector = RunProjector({})
    projector.apply(ReaderBatch([record], [], initial_index_complete=True))
    events = projector.snapshot()["timeline"]
    assert len(events) == 2
    assert events[0]["task"]["available"] is False
    assert events[1]["task"]["brief"] == "audit the evidence"

def test_collaboration_event_ids_are_stable_across_replay(tmp_path):
    record = _wire_record(tmp_path, {
        "role": "assistant",
        "tool_calls": [{"function": {
            "name": "searcher",
            "arguments": {"brief": "find consumers"},
        }}],
    })
    snapshots = []
    for _ in range(2):
        projector = RunProjector({})
        projector.apply(ReaderBatch(
            [record], [], initial_index_complete=True))
        snapshots.append(projector.snapshot()["timeline"])
    assert snapshots[0] == snapshots[1]
    assert snapshots[0][0]["id"] == "wire:40:index-0"
```

- [ ] **Step 5: Run projector tests and make them pass**

```bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_projector.py -q
```

Expected: all projector tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add scientist/ui/projector.py tests/scientist/ui/test_projector.py
git commit -m "feat: project scientist collaboration tasks"
```

---

### Task 2: Render Semantic Task Details and Preserve Raw Evidence

**Files:**

- Modify: `scientist/ui/static/app.js`
- Modify: `scientist/ui/static/style.css`
- Test: `tests/scientist/ui/test_server.py`

**Interfaces:**

- Add `showActivityDetail(event)` for semantic task fields already present in the snapshot.
- Keep `showDetail(detailId)` as the only raw-record fetch path.
- Collaboration timeline rows expose “查看活动详情”; other rows keep “查看原始记录”.
- The semantic detail panel exposes “查看原始记录” when the event has a `detail_ref`.

- [ ] **Step 1: Add failing frontend-contract assertions**

Extend `test_frontend_has_required_regions_and_safe_rendering_contract`:

```python
assert "showActivityDetail" in app
assert "activity.task" in app
assert "任务详情不可解析" in app
assert "完成标准不可用" in app
assert "查看活动详情" in app
assert "查看原始记录" in app
assert ".textContent" in app
assert ".innerHTML" not in app
```

Add a projector-backed snapshot assertion using a hostile brief such as `<img src=x onerror=alert(1)>`; verify the API returns it as data and the JavaScript contains no HTML insertion API.

- [ ] **Step 2: Run the frontend-contract tests and verify failure**

```bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui/test_server.py::test_frontend_has_required_regions_and_safe_rendering_contract -q
```

Expected: failure because `showActivityDetail` and the semantic action labels do not exist.

- [ ] **Step 3: Implement semantic task detail rendering**

In `renderTimeline`, branch on `event.kind === 'collaboration_task'`: bind “查看活动详情” to `showActivityDetail(event)`; leave current raw-detail behavior unchanged for other events.

Implement `showActivityDetail` exclusively with `element()` and `replaceChildren()`:

```javascript
function showActivityDetail(activity) {
  selected = activity.id;
  const root = document.getElementById('details');
  const task = activity.task || {};
  const content = element('div', 'task-detail');
  content.append(element('p', 'detail-source',
    unavailable(activity.role) + ' 任务'));
  if (!task.available) {
    content.append(element('p', 'warning', '任务详情不可解析'));
  } else {
    content.append(
      element('h3', 'task-label', '任务'),
      element('pre', 'task-text', task.brief),
    );
    if (Object.prototype.hasOwnProperty.call(task, 'definition_of_done')) {
      content.append(
        element('h3', 'task-label', '完成标准'),
        element('pre', 'task-text',
          task.definition_of_done || '完成标准不可用'),
      );
    }
  }
  const detailId = (activity.detail_refs || [])[0];
  if (detailId) content.append(detailButton('查看原始记录', detailId));
  root.replaceChildren(content);
}
```

Use `pre-wrap`, inherited font, and safe word wrapping for `.task-text`; add only the selectors required for spacing and readability.

- [ ] **Step 4: Run JavaScript and Observatory tests**

```bash
node --check scientist/ui/static/app.js
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui -q
```

Expected: JavaScript syntax check succeeds and all UI tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add scientist/ui/static/app.js scientist/ui/static/style.css tests/scientist/ui/test_server.py
git commit -m "feat: show collaboration task details"
```

---

### Task 3: Real-Run and Regression Verification

**Files:** No code changes expected.

**Interfaces:** Verifies the feature against the repository test suite and the existing `runs/singlenode/omilrec-v100-r3-scientist` record.

- [ ] **Step 1: Run the complete Scientist regression suite**

```bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/ui tests/scientist -q
```

Expected: all tests pass.

- [ ] **Step 2: Verify a real Executor task projection without writing the run**

Run a short Python invocation that constructs `RunReader` and `RunProjector` for `runs/singlenode/omilrec-v100-r3-scientist`, polls until initial indexing completes, then asserts at least one `collaboration_task` has role `executor`, a non-empty `task.brief`, a non-empty `task.definition_of_done`, and one `detail_ref`. Record the number of projected collaboration tasks.

- [ ] **Step 3: Verify repository hygiene**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional feature commits differ from the feature base, and the observed run has no changes.
