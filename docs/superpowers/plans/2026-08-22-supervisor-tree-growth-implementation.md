# Supervisor Tree-Growth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stateless one-shot Supervisor allocation model with the persistent, event-driven, sole growth gate defined in `docs/superpowers/specs/2026-08-22-supervisor-tree-growth-design.md`.

**Architecture:** One logical Supervisor identity per run (session.jsonl / notebook.md / meta.json under `run_dir/supervisor/session/`, reusing `ScientistSession`). Wake-up is driven only by durable evidence events (root ready, Experiment terminal, lease terminal without Experiment, goal/budget change) delivered as incremental `(from, to]` batches. The wake message carries facts and stable IDs only; the Supervisor investigates through read-only tools (`inspect_node`, `compare_nodes`, `lineage`, `search_experiments`, `inspect_experiment`, `inspect_originating_research_state`, `list_nodes`, `inspect_node_allocations`, `inspect_run_status`). Output contracts are three mutually exclusive terminals — `submit_growth_decision {node_ids, rationale}` (the normal path; empty selection waits/quiesces), `submit_integration_request`, `submit_epoch_review`. The Scheduler applies each decision in one transaction: CAS on the event cursor, decision row, cursor advance, proposer leases, audit event. No Frontier fallback exists in a Supervisor run; retries are bounded, exhaustion records `supervisor_stalled` and parks the run visibly. Quiescence additionally requires zero pending events and no stall. Frontier allocation remains only as the explicit baseline mode for non-Supervisor (ablation/GEPA) runs.

**Tech Stack:** Python 3, pytest, existing SQLite store / Scheduler / AgentRuntime / job envelope.

## Global Constraints

- Scheduler stays the sole SQLite writer; the supervisor event cursor advances only inside the decision-commit transaction (session `meta.json` is an audit mirror).
- Capacity never wakes the Supervisor; it is only a commit-time constraint. Unused capacity stays idle by design.
- Never fall back to Frontier allocation inside a Supervisor run (invariants 2 and 12). Failure keeps the batch unconsumed and retries the same logical session, bounded.
- Integration triggering and epoch review stay separate terminals, never bundled with `node_ids` (design §3.4).
- The existing Scientist / Integrator / Executor / Gate responsibilities are not expanded.
- Each milestone lands with its focused tests green; commit only after the relevant suite passes.

---

### Task 1: Finalize specs (M0)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-supervisor-tree-growth-design.md` (already amended in worktree)
- Modify: `docs/superpowers/specs/2026-08-22-supervisor-integrator-agent-runtime-design.md`
- Add: this plan

- [x] **Step 1: Commit the sharpened tree-growth spec**
- [x] **Step 2: Add a supersession header to the stateless-runtime spec** voiding §6.3 (stateless session), §8.1 (GroupSnapshot input), §8.2 (wide decision output), §5.5 + §8.5 (Frontier fallback), §17 MVP item 3, and the §15.3 fallback test
- [x] **Step 3: Commit docs** — `docs: finalize tree-growth spec and supersede stateless model`

### Task 2: Durable event log, cursor, decisions (M1)

**Files:**
- Modify: `simpleevo/db/schema.py`, `simpleevo/db/store.py`, `tests/db/test_store.py`

**Interfaces:**
- Produces tables: `supervisor_events(event_id PK AUTOINCREMENT, type, payload, created_at)`, `supervisor_cursor(consumer PK, last_consumed_event_id)`, `supervisor_decisions(decision_id PK, work_id, event_cursor_to, node_ids, rationale, outcome, created_at)`.
- Produces store API: `emit_supervisor_event(type, payload) -> event_id`; `pending_supervisor_events() -> list`; `supervisor_event_head() -> int`; `commit_supervisor_decision(...)` — one transaction: CAS `head == cursor_to` (else raise `StaleDecision`), idempotent on existing `decision_id`, insert decision row, advance cursor, create proposer allocations, record `supervisor_decision_accepted`.

- [ ] **Step 1: Failing store tests** — event order/durability, CAS rejects stale cursor, idempotent decision replay creates no duplicate leases, empty selection consumes the cursor
- [ ] **Step 2: Implement schema + store API**
- [ ] **Step 3: Focused suite green; commit** — `feat: durable supervisor wake events and decisions store`

### Task 3: Event emission points (M2)

**Files:**
- Modify: `simpleevo/scheduler/loop.py`, `simpleevo/scheduler/reconcile.py`, `tests/scheduler/test_reconcile.py`

- [ ] **Step 1: Emit `root_ready`** on first step when the root exists and the event log is empty
- [ ] **Step 2: Emit `experiment_terminal`** where an Experiment reaches a terminal status (completed / gate_rejected / no_change / failed), same transaction as the status write
- [ ] **Step 3: Emit `lease_terminal`** where a proposer lease closes without producing any Experiment (abstain / error / zero proposals)
- [ ] **Step 4: Emit terminal events from `Reconciler._mark_infra_failed`** for experiment/proposer kinds (lost jobs wake the gate); supervisor/integrator infra-failures emit nothing
- [ ] **Step 5: Focused suite green; commit** — `feat: durable supervisor wake events with reconcile coverage`

### Task 4: Supervisor investigation tools (M3)

**Files:**
- Modify: `proposer/l2_memory.py`, `proposer/supervisor.py`, `tests/proposer/test_l2_memory.py`

**Interfaces:**
- Produces: `L2MemoryService.node_allocations(node_id)` and `L2MemoryService.run_status()` (mechanical facts only, no ranking).
- Produces: `SupervisorTools` registry in `proposer/supervisor.py` exposing the nine read-only actions; `execute` never raises (`{"ok": False, ...}` failures).

- [ ] **Step 1: Wire the six evidence tools** (`inspect_node`, `compare_nodes`, `lineage`, `search_experiments`, `inspect_experiment`, `inspect_originating_research_state` with its inspect-first gate)
- [ ] **Step 2: Add the three global views** (`list_nodes` over all history with a mechanical allocatable flag, `inspect_node_allocations`, `inspect_run_status`)
- [ ] **Step 3: Tool-contract text block** for the Supervisor prompt (self-contained, not mixed into Scientist specs)
- [ ] **Step 4: Focused suite green; commit** — `feat: read-only supervisor investigation tools`

### Task 5: Persistent SupervisorAgent (M4)

**Files:**
- Modify: `proposer/supervisor.py`, `proposer/prompts/supervisor.md`, `tests/proposer/test_supervisor_agent.py`

**Interfaces:**
- Produces: `SupervisorSession.load_or_create(run_dir)` over `ScientistSession._load_from_dir(run_dir/"supervisor"/"session")`; `meta.json` gains an `event_cursor` audit mirror.
- Produces: `SupervisorAgent.resume(batch, session)` — system prompt (gate identity + tool contract + notebook-as-revisable-autobiography) + notebook + incremental event batch; cold-start note on the first turn; no raw tool-observation replay.
- Produces: terminals `submit_growth_decision {node_ids, rationale}` / `submit_integration_request` / `submit_epoch_review`, mutually exclusive per turn, each consuming the batch.
- Produces: `compact` from `ContextPolicy.from_config` + scientist.py compaction helpers; `checkpoint` = notebook rewrite model call on terminal and budget exhaustion.

- [ ] **Step 1: Failing agent tests** — terminal contract rejects extra fields; notebook checkpoint writes; tool dispatch; session continuity across two turns
- [ ] **Step 2: Implement; delete `_NoTools`, `decide()`, `GroupSnapshot`/watermark; keep `validate_integration_request`**
- [ ] **Step 3: Rewrite `prompts/supervisor.md`** to the growth-gate identity (design §2)
- [ ] **Step 4: Focused suite green; commit** — `feat: persistent tool-using supervisor agent`

### Task 6: Event-batch worker CLI (M5)

**Files:**
- Modify: `proposer/supervisor_cli.py`, tests

- [ ] **Step 1: Payload becomes `{events, cursor_from, cursor_to, run_dir, researcher, agent_timeout_seconds, supervisor_steps, attempt_id}`; session loads from `run_dir/supervisor/session/`**
- [ ] **Step 2: Result envelope `{decision_id, decision_kind, node_ids, rationale, event_cursor_to}`** with identity/cursor supplied by the harness
- [ ] **Step 3: Focused suite green; commit** — `feat: event-batch supervisor worker with persistent session`

### Task 7: Scheduler gate rewiring (M6)

**Files:**
- Modify: `simpleevo/scheduler/loop.py`, `tests/scheduler/test_supervisor.py`, `tests/test_integration.py`, `tests/test_scheduler_reseed.py`, `tests/test_node_proposal_budget.py`

- [ ] **Step 1: Remove the `supervisor_decider` injection and the in-process path**
- [ ] **Step 2: Rewrite `_supervisor_job_directives` as `_run_supervisor_gate`** — submit when pending events exist and no worker is running (`work_id = supervisor-<head>`; same batch retries reuse the id); ingest → mechanical validation → `commit_supervisor_decision`; `StaleDecision` archives the artifact and records `supervisor_decision_stale` without consuming; invalid output records `supervisor_decision_rejected` and retries the same session; success creates leases via the existing creation block with config `proposal_slots`; empty selection consumes without leasing**
- [ ] **Step 3: Bounded retries** — attempts per work id capped by config; exhaustion records `supervisor_stalled` once and stops resubmitting; `run()` returns `{"status": "stalled"}` instead of silently quiescing; step telemetry carries `supervisor_pending`**
- [ ] **Step 4: Quiescence hardening** — in Supervisor mode `_quiescent()` additionally requires zero pending events and no stall; Frontier path remains only when `submit_supervisor is None` (explicit baseline mode)**
- [ ] **Step 5: Focused + integration suites green; commit** — `feat: event-driven supervisor gate with no frontier fallback`

### Task 8: Config and chore (M7)

**Files:**
- Modify: `simpleevo/config.py`, `tests/test_config.py`, `examples/xsbench_opt/task-supervisor.yaml`, `examples/xsbench_opt/task-supervisor-branch.yaml`, `README.md`, `scripts/run_supervisor_test.py`

- [ ] **Step 1: `supervisor_steps: int = 40`, `supervisor_max_retries: int = 3`** with round-trip serialization tests
- [ ] **Step 2: Update example headers (no fallback wording, new knobs), README supervisor description, driver event stream to the new event types**
- [ ] **Step 3: Suite green; commit** — `chore: supervisor runtime knobs and docs`

### Task 9: Invariant test matrix (M8)

**Files:**
- Modify: `tests/scheduler/test_supervisor.py`, `tests/proposer/test_supervisor_agent.py`, `tests/test_integration.py`
- Add: focused cases mapping one-to-one onto the design's 14 acceptance invariants

- [ ] **Step 1: Invariants 1/2/12** — every lease links to a decision row; N worker failures produce zero Frontier leases, idle capacity, and `supervisor_stalled`
- [ ] **Step 2: Invariants 3/13** — two wake-ups share identity/notebook; compaction keeps the archive append-only and resumes from notebook + new events
- [ ] **Step 3: Invariants 4/5** — events durable before delivery; kill-and-restart redelivers the same batch; decision replay creates no duplicate leases
- [ ] **Step 4: Invariants 6/7/8** — wake payload schema allowlist (no ranking); `list_nodes` sees parked/prior-epoch nodes; a historical node unrelated to the newest event may be selected
- [ ] **Step 5: Invariants 9/10/11** — 0/1/N selections; no re-ask without new terminal events; empty selection waits with work in flight, quiesces without; pending events block quiescence
- [ ] **Step 6: Invariant 14** — output contains only `node_ids` and `rationale`
- [ ] **Step 7: Full suite green; commit** — `test: cover tree-growth supervisor invariants`

## Verification

```bash
source /datafs/users/wujxy/py_venv/my_env/bin/activate
python -m pytest -q --deselect tests/test_example_config.py::test_example_eval_commands_emit_parsable_metrics
```

Then check the 14-invariant matrix task-by-task. An optional real smoke run via `scripts/run_supervisor_test.py` (API cost; nested-apptainer: unset `APPTAINER_BIND`, `--userns`) verifies wake counts equal terminal-event batches, no fallback, waiting on empty selections, and a distinguishable stalled state.
