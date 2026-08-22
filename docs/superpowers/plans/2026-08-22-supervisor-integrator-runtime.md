# Supervisor, Integrator, and Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SimpleEvolution an independently testable Supervisor allocation loop, Scientist `EXPLORE`/`SYNTHESIZE` protocol, temporary Integrator workflow, and logical epoch promotion without changing the existing Executor path.

**Architecture:** Extract the current reusable model/tool/session loop into `proposer/agent_runtime.py`, retaining `ScientistAgent` as its Scientist-specific role adapter. Extend the L2 store with minimal proposal provenance and epoch/integration records; Scheduler remains the sole writer and turns Supervisor/Integrator artifacts into existing allocations and experiment jobs. Frontier is invoked only when no valid Supervisor decision is available.

**Tech Stack:** Python 3, SQLite, pytest, existing local/HTCondor job envelopes, existing `claude -p` Executor.

## Global Constraints

- Keep Node structurally single-parent; donor Experiment IDs are provenance only.
- Keep Executor on the existing `claude -p` job path.
- Keep Supervisor stateless and deny Supervisor/Integrator access to Scientist private sessions.
- Persist only epochs, integration requests, proposal operation, and proposal donor IDs; use existing attempts, allocations, and scheduler events for the rest.
- All new production behavior starts with a failing pytest and ends with focused tests plus the relevant regression suite.

---

### Task 1: Persist operations, integration requests, and epochs

**Files:**
- Modify: `simpleevo/db/schema.py`, `simpleevo/db/store.py`, `simpleevo/db/queries.py`
- Test: `tests/db/test_schema.py`, `tests/db/test_store.py`

**Interfaces:**
- Produces immutable `Proposal.research_operation: str | None` and `Proposal.donor_experiment_ids: tuple[str, ...]`.
- Produces Store methods `current_epoch()`, `create_epoch(root_node_id, previous_epoch_id)`, `create_integration_request(...)`, `get_integration_request(id)`, and idempotent request transitions.

- [ ] Write failing tests proving a migration creates `epoch-0` at the original root, a synthesized proposal requires donor IDs, and an explore proposal rejects them.
- [ ] Run `python -m pytest tests/db/test_schema.py tests/db/test_store.py -q`; confirm the new tests fail on absent fields/methods.
- [ ] Add only the columns/tables and Store dataclasses/methods named above. Validate operation/donor combinations before write and derive promotion Experiment from `epochs.root_node_id` rather than duplicating it.
- [ ] Re-run the focused DB tests; confirm pass.
- [ ] Commit `feat: persist research operations and epochs`.

### Task 2: Make the Scientist terminal protocol explicit

**Files:**
- Modify: `proposer/scientist.py`, `proposer/prompts/*`, `proposer/l2_memory.py`, `proposer/memory/models.py`, `proposer/cli.py`, `simpleevo/db/store.py`
- Test: `tests/proposer/test_scientist_session.py`, `tests/proposer/test_research_state_tools.py`, `tests/db/test_store.py`

**Interfaces:**
- `submit_explorations` accepts `1..proposal_slots` distinct proposals and writes `research_operation="explore"` with no donors.
- `submit_synthesis` accepts exactly one proposal and `donor_experiment_ids`, after those Experiments were inspected, and writes `research_operation="synthesize"`.
- `abstain` remains a terminal result.

- [ ] Write failing parser/tool tests for multiple explores, a single donor-backed synthesis, mixed terminal actions, uninspected donors, and synthesis with two proposals.
- [ ] Run those tests and confirm each fails for the new missing contract.
- [ ] Implement the smallest action parsing/validation changes; map both terminal forms to the existing proposal result envelope and preserve current proposal IDs/session behavior.
- [ ] Update the Scientist prompt so `EXPLORE` requires independently testable mechanisms and `SYNTHESIZE` prohibits unrelated new mechanisms.
- [ ] Run proposer and DB focused tests; confirm pass.
- [ ] Commit `feat: add scientist explore and synthesize operations`.

### Task 3: Extract the common cognitive runtime without changing Scientist behavior

**Files:**
- Create: `proposer/agent_runtime.py`
- Modify: `proposer/research_agent.py`, `proposer/scientist.py`, `proposer/orchestrator.py`
- Test: `tests/proposer/test_agent_runtime.py`, existing `tests/proposer/test_scientist_session.py`

**Interfaces:**
- `AgentRole` provides `build_context(task, session)`, `build_tools(task)`, and `handle_terminal(action, state)`.
- `AgentRuntime.run(role, task, session)` owns model invocation, action parsing, tool dispatch, compaction, session append/resume, trace, budgets, and common errors; terminal handling returns the role's typed result.

- [ ] Write a failing fake-model test showing the runtime dispatches an allowed tool, compacts whole action/observation pairs, and rejects another role’s terminal action.
- [ ] Run it and confirm the shared runtime module is absent.
- [ ] Move only common loop code from `ResearchAgent`/`ScientistAgent` to `AgentRuntime`; make `ScientistAgent` a thin role adapter. Do not rename `proposer/runtime.py` or alter Executor invocation.
- [ ] Run new runtime tests plus the full proposer test directory; confirm the existing Scientist result JSON remains compatible.
- [ ] Commit `refactor: extract shared agent runtime`.

### Task 4: Add stateless Supervisor jobs and allocation ingestion

**Files:**
- Create: `proposer/supervisor.py`, `proposer/prompts/supervisor.md`
- Modify: `simpleevo/jobs/base.py`, `simpleevo/jobs/local.py`, `simpleevo/jobs/condor.py`, `simpleevo/scheduler/loop.py`, `simpleevo/scheduler/reconcile.py`, `simpleevo/db/store.py`, `simpleevo/db/queries.py`
- Test: `tests/scheduler/test_supervisor.py`, `tests/jobs/test_envelope.py`, `tests/scheduler/test_reconcile.py`

**Interfaces:**
- `GroupSnapshot` contains all mechanically eligible Nodes (not merely Frontier), objective facts, usage facts, epoch/integration refs, and a watermark.
- `SupervisorDecision(decision_id, epoch_id, snapshot_watermark, allocations, integration_request, rationale, evidence_refs)` is ingested idempotently into existing allocations/events.
- `BaseSubmitter.submit_supervisor(work_id, payload)` follows the same artifact contract as proposer jobs.

- [ ] Write failing scheduler tests that a valid Supervisor decision can select an eligible dormant/non-Frontier Node, retains unselected Nodes, rejects stale/invalid allocations, and falls back to deterministic Frontier only after a failed/invalid Supervisor job.
- [ ] Run the tests and confirm failure because no Supervisor path exists.
- [ ] Implement snapshot building, a stateless Role using the shared runtime, Supervisor artifact submission/ingestion, and decision-to-allocation validation. Store accepted/rejected decisions as scheduler events, not a new decisions table.
- [ ] Route proposer capacity through accepted decisions; preserve old Frontier computation for telemetry and fallback only.
- [ ] Run scheduler/jobs focused tests and existing frontier regressions; confirm pass.
- [ ] Commit `feat: allocate proposer work through supervisor decisions`.

### Task 5: Add the temporary Integrator and normal-experiment synthesis path

**Files:**
- Create: `proposer/integrator.py`, `proposer/prompts/integrator.md`
- Modify: `simpleevo/jobs/base.py`, `simpleevo/jobs/local.py`, `simpleevo/jobs/condor.py`, `simpleevo/scheduler/loop.py`, `simpleevo/scheduler/reconcile.py`, `simpleevo/db/store.py`, `proposer/agent_runtime.py`
- Test: `tests/proposer/test_integrator.py`, `tests/scheduler/test_integration_requests.py`, `tests/jobs/test_envelope.py`

**Interfaces:**
- `IntegrationRequest` has `integration_request_id`, `epoch_id`, `target_node_id`, `donor_experiment_ids`, `selection_rationale`, plus lifecycle links needed for recovery.
- Integrator returns either the existing one-proposal `submit_synthesis` result or `abstain`; its donors must exactly be a subset of its request.
- `BaseSubmitter.submit_integrator(work_id, payload)` retains/reuses a request session only on retry.

- [ ] Write failing tests for gate-passed donor validation, fresh-session-per-request behavior, private-session denial, exact donor provenance, abstain closure, and normal Executor creation from target Node.
- [ ] Run the tests and confirm they fail on the missing workflow.
- [ ] Implement the Integrator Role and narrow read-only query tools; persist request state and schedule/reconcile its job. Reuse the existing experiment queue for submitted synthesis proposals.
- [ ] Run focused Integrator/scheduler/job tests; confirm pass.
- [ ] Commit `feat: add integration request workflow`.

### Task 6: Promote validated candidates into logical epochs and finish recovery/telemetry

**Files:**
- Modify: `proposer/supervisor.py`, `simpleevo/scheduler/loop.py`, `simpleevo/scheduler/reconcile.py`, `simpleevo/scheduler/telemetry.py`, `simpleevo/db/store.py`, `simpleevo/reporting/*`
- Test: `tests/scheduler/test_epoch_promotion.py`, `tests/scheduler/test_reconcile.py`, `tests/scheduler/test_research_state_telemetry.py`, `tests/reporting/test_data.py`

**Interfaces:**
- `EpochDecision(request_id, outcome, rationale, evidence_refs)` accepts only `promote` or `retain`.
- Promotion requires an open request, completed gate-passed Experiment, valid Child Node, and intact provenance; it appends a new epoch and leaves all historical Nodes unchanged.

- [ ] Write failing tests for rejected/no-change candidates never promoting, a successful candidate creating a new epoch root while old branches remain eligible, and restart recovery of an open request/current epoch.
- [ ] Run the tests and confirm the promotion APIs are absent.
- [ ] Add the narrow promotion reviewer job/result, mechanical precondition checks, request recovery, and the specified allocation/integration telemetry. Do not make telemetry a fitness signal.
- [ ] Run the scheduler/reporting focused suites, then `python -m pytest -q`; separately record the known pre-existing `test_example_config` failure if it remains.
- [ ] Commit `feat: promote validated integration epochs`.

### Task 7: End-to-end regression and documentation

**Files:**
- Modify: `README.md`, relevant `docs/design/*`, example configuration only if a new role endpoint needs an explicit default
- Test: `tests/test_integration.py`, `tests/test_scheduler_attempts.py`

- [ ] Write/adjust an end-to-end fake-backend test demonstrating: Supervisor allocates a divergent low-base Node, Scientist explores/synthesizes, Integrator creates a candidate through the normal Executor, and promotion leaves old nodes revivable.
- [ ] Run it red, implement only wiring/documentation required for the scenario, then run it green.
- [ ] Run `python -m pytest -q`, `git diff --check`, and inspect the worktree status.
- [ ] Commit `test: cover supervisor integrator workflow` and prepare the branch for review/merge.
