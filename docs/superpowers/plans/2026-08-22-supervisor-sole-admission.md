# Supervisor Sole Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Supervisor-granted Scientist leases the sole normal research-admission decision by removing Frontier's second veto from the Executor queue.

**Architecture:** A proposal is admitted once when `publish_research_batch` validates that its ID was reserved by a legal Scientist allocation or integration request. `ExecutorQueue` then provides only bounded FIFO backpressure; Frontier remains computed for telemetry and Supervisor-failure fallback, but is not passed into the queue. Integrator remains a request-scoped synthesis-only Scientist whose proposal and Executor experiment share the request target Node SHA.

**Tech Stack:** Python 3, pytest, existing SQLite proposal lifecycle and Scheduler.

## Global Constraints

- Do not delete or redesign the modular Frontier policies, reporting, or fallback allocation path.
- Do not add tables, columns, configuration fields, or agent roles.
- Do not change Integrator, Scientist, or Executor workspace SHA selection.
- Preserve FIFO dequeue and `max_size` overflow behavior.
- Start production changes with failing focused tests and run the relevant regression suite afterward.

---

### Task 1: Remove Frontier's Executor-queue veto

**Files:**
- Modify: `tests/scheduler/test_queue.py`
- Modify: `tests/scheduler/test_supervisor.py`
- Modify: `tests/scheduler/test_integration_requests.py`
- Modify: `tests/test_integration.py`
- Modify: `simpleevo/scheduler/queue.py`
- Modify: `simpleevo/scheduler/loop.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: proposals already validated and persisted with `status="queued"`.
- Produces: `ExecutorQueue(store, config)` with `dequeue`, `size`, and `enforce_bound`; no Frontier argument and no `cleanup` method.
- Produces: `Scheduler._drain_executor_queue()` without a Frontier argument.

- [ ] **Step 1: Write failing queue and scheduler tests**

Replace the old Frontier-cleanup test with a test that constructs the queue without a Frontier and confirms all valid queued proposals remain FIFO-eligible:

```python
def test_queue_does_not_second_guess_admitted_proposals(store: ResearchStore):
    _seed(store)
    queue = ExecutorQueue(store, QueueConfig(max_size=10))

    assert queue.dequeue(10) == ["p1", "p2"]
```

Add a scheduler regression in `tests/scheduler/test_supervisor.py` that seeds a queued proposal on a node absent from `Frontier`, invokes `_drain_executor_queue()`, and asserts the submitted experiment payload uses that node ID and SHA:

```python
jobs = scheduler._drain_executor_queue()

assert len(jobs) == 1
assert submitted[0][1]["parent_node_id"] == dormant.node_id
assert submitted[0][1]["parent_sha"] == dormant.sha
assert store.get_proposal("non-frontier-proposal").status == "running"
```

Update existing queue construction and `_drain_executor_queue` calls to the new signatures, without changing their assertions.

- [ ] **Step 2: Run focused tests and verify the new contract is red**

Run:

```bash
source /datafs/users/wujxy/py_venv/my_env/bin/activate
python -m pytest tests/scheduler/test_queue.py tests/scheduler/test_supervisor.py tests/scheduler/test_integration_requests.py tests/test_integration.py -q
```

Expected: failures reporting that `ExecutorQueue` still requires `frontier`, `_drain_executor_queue` still requires an argument, or a non-Frontier proposal becomes dormant.

- [ ] **Step 3: Implement the minimal queue boundary**

Change `ExecutorQueue` to accept only the store and queue config, and remove the Frontier-specific state and cleanup path:

```python
class ExecutorQueue:
    """Mechanically bounded FIFO over already-admitted proposals."""

    def __init__(self, store, config: QueueConfig):
        self._store = store
        self._config = config
```

Keep `dequeue`, `size`, and `enforce_bound` unchanged. Delete `cleanup()` because no caller should demote an admitted proposal based on a later scoring view.

Change Scheduler to construct and drain the queue without Frontier:

```python
def _drain_executor_queue(self):
    """Submit admitted queued proposals as experiment jobs up to capacity."""
    queue = ExecutorQueue(self.store, self.config.queue or QueueConfig())
    queue.enforce_bound()
```

Change `step()` to call `_drain_executor_queue()`; remove the obsolete `integration_targets` exception because integration proposals now follow the same admitted-proposal path as Scientist proposals.

- [ ] **Step 4: Clarify the public architecture description**

Update the README introduction to state that Supervisor grants normal Scientist admission, Frontier is telemetry/failure fallback only, and Executor consumes already-admitted proposals through bounded FIFO. Describe Integrator as a request-scoped synthesis-only Scientist; do not introduce new configuration.

- [ ] **Step 5: Run focused and full verification**

Run:

```bash
source /datafs/users/wujxy/py_venv/my_env/bin/activate
python -m pytest tests/scheduler/test_queue.py tests/scheduler/test_supervisor.py tests/scheduler/test_integration_requests.py tests/test_integration.py -q
python -m pytest -q --deselect tests/test_example_config.py::test_example_eval_commands_emit_parsable_metrics
git diff --check
```

Expected: focused suite passes; full suite reports all selected tests passing; `git diff --check` emits no output. The pre-existing example-config baseline remains explicitly deselected.

- [ ] **Step 6: Commit the implementation**

```bash
git add README.md simpleevo/scheduler/queue.py simpleevo/scheduler/loop.py tests/scheduler/test_queue.py tests/scheduler/test_supervisor.py tests/scheduler/test_integration_requests.py tests/test_integration.py docs/superpowers/plans/2026-08-22-supervisor-sole-admission.md
git commit -m "fix: make supervisor the sole research gate"
```
