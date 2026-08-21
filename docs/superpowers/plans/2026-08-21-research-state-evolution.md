# Research State Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `ResearchState` and `CognitiveTransformation` as the two cognitive cores connecting Scientist understanding, Proposal, Experiment, and Child Node while retaining one objective Node Tree.

**Architecture:** Proposer workers keep ResearchState and CognitiveTransformation records in round-local memory and publish them in their durable result artifact; the Scheduler remains the only L2 writer and atomically ingests the cognitive records with their Proposals. `transform_worldview` uses one generator in a short stateless model call, while the existing Scheduler/context path deterministically assembles the originating state plus experiment facts for Child startup.

**Tech Stack:** Python 3.9+, dataclasses, SQLite, pytest, existing ChatModel/Scientist/Scheduler runtime.

## Global Constraints

- Execute on the existing `research_state_evolve` branch, which descends from design commit `c79caa9` and includes this plan before implementation starts.
- At execution time, invoke `using-git-worktrees` before editing and create an isolated worktree for `research_state_evolve`; do not disturb the dirty `main` checkout.
- Preserve the Scheduler as the sole writer of `simpleevo.db`; HTCondor/local proposer workers only emit durable result artifacts.
- Keep one objective Node Tree and the existing measured-axis Frontier; do not add a ResearchState Frontier or semantic similarity judge.
- Implement exactly two cognitive cores: `ResearchState` and `CognitiveTransformation`; Proposal/Experiment/Node remain the existing objective action-and-feedback path.
- `working_model` is free-form Scientist text. Do not add mandatory assumptions, root-cause, causal-model, hypothesis-family, or validation-status fields.
- Every new Proposal must reference exactly one ResearchState; one ResearchState may produce zero or more Proposals.
- “Prefer one Proposal from each viable ResearchState before adding a second” remains prompt guidance, never a schema constraint.
- `evidence_refs` remains optional provenance only. Do not add claim extraction, claim-to-evidence verification, reference validation, method-code alignment checks, proof packs, or an Evidence Compiler; Chain-of-Evidence is future work.
- ResearchStateSeed assembly only follows existing identity links and formats Child context; it must not resolve or validate evidence or infer supported, contradicted, validated, or recommended.
- One `transform_worldview` invocation applies exactly one generator. Composition happens through multiple recorded transformations.
- `proposal_slots` remains a per-Episode ceiling; `max_proposals_per_node` is the lifetime Node ceiling. Neither is a quota.
- Preserve legacy SQLite run directories: old Proposal rows may have `research_state_id = NULL`, but all newly ingested Proposals must have a non-null ResearchState.
- Add no external dependency.
- Follow TDD: observe each new test fail before writing the minimal implementation; run focused tests after every change and the full suite before completion.

## File Structure

**Create:**

- `simpleevo/research_state.py` — shared immutable ResearchState and CognitiveTransformation records plus JSON conversion.
- `proposer/cognitive_transformer.py` — one-shot generator application through the existing `ChatModel` boundary.
- `tests/db/test_research_state_store.py` — L2 schema, migration, persistence, and atomic-ingest tests.
- `tests/proposer/test_research_state_tools.py` — action parsing, local registration, generator application, and Proposal linkage tests.
- `tests/test_research_state_seed.py` — root/Child seed assembly tests.
- `tests/test_node_proposal_budget.py` — lifetime budget and concurrent reservation tests.
- `tests/scheduler/test_research_state_telemetry.py` — cognitive-width telemetry tests.

**Modify:**

- `simpleevo/db/schema.py` — ResearchState/Transformation tables and backward-compatible Proposal column migration.
- `simpleevo/db/store.py` — domain persistence, atomic research batch ingest, and Node-level proposal reservations.
- `simpleevo/db/queries.py` — read projections for ResearchState, Transformation, and telemetry.
- `proposer/research_agent.py` — round-local cognitive records and trace/telemetry fields.
- `proposer/research_tools.py` — register/transform tool schemas and dispatch.
- `proposer/memory/models.py` — Proposal reference to ResearchState and preregistered expectation.
- `proposer/scientist.py` — protocol parsing, guard validation, tool wiring, prompt contract, and result propagation.
- `proposer/orchestrator.py` — EpisodeResult cognitive payload and Child seed handoff.
- `proposer/cli.py` — worker payload/result serialization and branch-specific session inheritance.
- `proposer/context.py` — render `ResearchStateSeed` as Scientist startup context.
- `simpleevo/generator.py` — explicit single-generator selection helper.
- `simpleevo/config.py` — `max_proposals_per_node` configuration.
- `simpleevo/scheduler/loop.py` — allocation budget, generator payload, atomic cognitive ingest, and minimal Child seed assembly.
- `simpleevo/scheduler/telemetry.py` — ResearchState width and Proposal concentration telemetry.
- `proposer/prompts/proposer.md` — working-model ownership and soft breadth guidance.
- `tests/db/test_schema.py`, `tests/db/test_store.py`, `tests/test_generator.py`, `tests/test_scheduler_reseed.py`, `tests/test_config.py`, `tests/test_integration.py`, `tests/scheduler/test_frontier_persistence.py` — update existing contracts and regression coverage.
- `examples/tiny_algo_opt/task.yaml`, `examples/tiny_algo_opt/task.condor.yaml`, `examples/xsbench_opt/task.yaml`, `examples/xsbench_opt/task-fractal.yaml`, `examples/omilrec_opt/task.yaml` — explicit Node proposal budgets.
- `docs/design/evolution_for_research_state.md` — add the accepted “stateless mentor consultation” generator mechanics.

---

### Task 1: Shared ResearchState model and L2 persistence

**Files:**

- Create: `simpleevo/research_state.py`
- Create: `tests/db/test_research_state_store.py`
- Modify: `simpleevo/db/schema.py`
- Modify: `simpleevo/db/store.py`
- Modify: `simpleevo/db/queries.py`
- Modify: `tests/db/test_schema.py`
- Modify: `tests/db/test_store.py`
- Modify: `tests/scheduler/test_frontier_persistence.py`

**Interfaces:**

- Produces `ResearchState`, `CognitiveTransformation`, `research_state_to_dict()`, and `transformation_to_dict()` in `simpleevo.research_state`.
- Produces `ResearchQueries.get_research_state()`, `research_states_for_episode()`, and `get_transformation()`.
- Extends `Proposal` with `research_state_id: str | None` at the end of the dataclass so legacy constructors can opt in gradually.
- New cross-cognitive IDs are application-validated TEXT links; Node/Episode ownership retains SQLite foreign keys.

- [ ] **Step 1: Write failing schema and round-trip tests**

Add tests that create a Node/Episode, persist a transformation and two immutable ResearchStates, then read them through `ResearchQueries`:

```python
def test_research_state_and_transformation_round_trip(store: ResearchStore):
    with store.transaction() as tx:
        node = tx.create_node(
            parent_node_id=None, experiment_id=None, sha="root",
            metrics={}, gate_result=_gate(True), depth=0, status="active",
        )
        episode = tx.create_episode(node_id=node.node_id)
        transformation = tx.create_cognitive_transformation(
            CognitiveTransformation(
                transformation_id="ct-episode-1-001",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                source_research_state_id=None,
                operator_id="G2",
                challenge="Question the current component boundary.",
                created_at=1.0,
            )
        )
        state = tx.create_research_state(
            ResearchState(
                research_state_id="rs-episode-1-001",
                node_id=node.node_id,
                episode_id=episode.episode_id,
                derived_from_research_state_id=None,
                transformation_id=transformation.transformation_id,
                working_model="The boundary loses reusable state.",
                evidence_refs=("source:src/fcn.cc:FCN",),
                created_at=2.0,
            )
        )

    queries = ResearchQueries(store.path)
    assert queries.get_transformation(transformation.transformation_id) == transformation
    assert queries.get_research_state(state.research_state_id) == state
    assert queries.research_states_for_episode(episode.episode_id) == [state]
```

Extend `test_schema_creates_all_tables()` with `research_states` and `cognitive_transformations`. Add a migration test that creates the old `proposals` table, calls `ResearchDBSchema.apply(conn)`, and asserts `PRAGMA table_info(proposals)` contains `research_state_id`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m pytest tests/db/test_research_state_store.py tests/db/test_schema.py -q
```

Expected: collection/import failure because `simpleevo.research_state` and the new store methods do not exist.

- [ ] **Step 3: Add the shared immutable records**

Create `simpleevo/research_state.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CognitiveTransformation:
    transformation_id: str
    node_id: str
    episode_id: str
    source_research_state_id: str | None
    operator_id: str
    challenge: str
    created_at: float


@dataclass(frozen=True)
class ResearchState:
    research_state_id: str
    node_id: str
    episode_id: str
    derived_from_research_state_id: str | None
    transformation_id: str | None
    working_model: str
    evidence_refs: tuple[str, ...]
    created_at: float


def transformation_to_dict(value: CognitiveTransformation) -> dict[str, Any]:
    return asdict(value)


def research_state_to_dict(value: ResearchState) -> dict[str, Any]:
    result = asdict(value)
    result["evidence_refs"] = list(value.evidence_refs)
    return result
```

- [ ] **Step 4: Add schema and legacy migration**

Add `cognitive_transformations` and `research_states` tables and indexes. Add a nullable `research_state_id` column to the fresh `proposals` DDL. In `ResearchDBSchema.apply()`, inspect `PRAGMA table_info(proposals)` and run exactly one idempotent migration for pre-feature databases:

```python
columns = {
    row[1] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()
}
if "research_state_id" not in columns:
    conn.execute("ALTER TABLE proposals ADD COLUMN research_state_id TEXT")
```

Use these table shapes:

```sql
CREATE TABLE IF NOT EXISTS cognitive_transformations (
    transformation_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    source_research_state_id TEXT,
    operator_id TEXT NOT NULL,
    challenge TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS research_states (
    research_state_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id),
    derived_from_research_state_id TEXT,
    transformation_id TEXT,
    working_model TEXT NOT NULL,
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
```

- [ ] **Step 5: Add store/query projections and legacy Proposal compatibility**

Import the shared records into `store.py`; add `_research_state_from_row()` and `_transformation_from_row()`; implement `_Transaction.create_*` and `get_*`. Add `research_state_id: str | None = None` as the final `Proposal` field, include it in INSERT/deserialization, and update direct `Proposal(...)` fixtures to pass `research_state_id=None` where positional compatibility is unclear.

Application validation in later tasks will enforce non-null for new worker results; legacy rows remain readable.

- [ ] **Step 6: Run focused storage tests and verify GREEN**

Run:

```bash
python -m pytest tests/db/test_research_state_store.py tests/db/test_schema.py tests/db/test_store.py tests/scheduler/test_frontier_persistence.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the storage slice**

```bash
git add simpleevo/research_state.py simpleevo/db/schema.py simpleevo/db/store.py simpleevo/db/queries.py tests/db/test_research_state_store.py tests/db/test_schema.py tests/db/test_store.py tests/scheduler/test_frontier_persistence.py
git commit -m "feat: persist research state provenance"
```

---

### Task 2: Scientist-local registration and stateless Cognitive Transformation

**Files:**

- Create: `proposer/cognitive_transformer.py`
- Create: `tests/proposer/test_research_state_tools.py`
- Modify: `proposer/research_agent.py`
- Modify: `proposer/research_tools.py`
- Modify: `proposer/scientist.py`
- Modify: `simpleevo/generator.py`
- Modify: `tests/test_generator.py`

**Interfaces:**

- Produces `CognitiveTransformer.transform(source_text, operator_id, used_operator_ids, timeout_seconds) -> tuple[str, str, object]`, returning `(resolved_operator_id, challenge, usage)`; `ResearchTools` alone assigns and records the Transformation ID.
- `WorkingState.research_states` and `.transformations` hold job-local records until Scheduler ingest.
- Adds non-terminal actions `register_research_state` and `transform_worldview`.
- IDs are host-generated and deterministic within an Episode: `rs-{episode_id}-{ordinal:03d}` and `ct-{episode_id}-{ordinal:03d}`; the model never supplies IDs.

- [ ] **Step 1: Write failing parser, registration, and transformer tests**

Cover these behaviors in `tests/proposer/test_research_state_tools.py`:

```python
def test_register_research_state_assigns_host_identity():
    state = WorkingState()
    tools = _tools(episode_id="ep-1", node_id="node-1")
    result = tools.execute(
        {
            "action": "register_research_state",
            "working_model": "Repeated work crosses the FCN boundary.",
            "evidence_refs": ["source:src/fcn.cc:FCN"],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["ok"] is True
    assert result["research_state_id"] == "rs-ep-1-001"
    assert state.research_states[result["research_state_id"]].node_id == "node-1"


def test_transform_worldview_uses_one_generator_and_records_challenge():
    model = FakeModel("Question whether FCN is the natural ownership boundary.")
    state = WorkingState()
    tools = _tools(model=model, episode_id="ep-1", node_id="node-1")
    result = tools.execute(
        {"action": "transform_worldview", "operator_id": "G2"},
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result["transformation_id"] == "ct-ep-1-001"
    assert state.transformations[result["transformation_id"]].operator_id == "G2"
    assert len(model.calls) == 1
    assert "Do not generate implementation proposals" in model.calls[0]["system"]
```

Also test rejection of an unknown generator, an unknown `derived_from_research_state_id`, an unknown `transformation_id`, an empty working model, and a `source_research_state_id` owned by another Episode.

- [ ] **Step 2: Run the tests and verify RED**

```bash
python -m pytest tests/proposer/test_research_state_tools.py tests/test_generator.py -q
```

Expected: failure because the actions and transformer do not exist.

- [ ] **Step 3: Extend round-local state and action schemas**

Add to `WorkingState`:

```python
research_states: dict[str, ResearchState] = field(default_factory=dict)
transformations: dict[str, CognitiveTransformation] = field(default_factory=dict)
```

Add `register_research_state` and `transform_worldview` to `RESEARCH_TOOL_SPECS`, `_RESEARCH_TOOL_ACTIONS`, `_dispatch()`, `_fingerprint()`, and `_action_summary()`. Parse only Scientist-owned inputs; do not accept identity fields.

Use these exact action contracts:

```json
{"action":"register_research_state","working_model":"Repeated work crosses the FCN boundary.","evidence_refs":["source:src/fcn.cc:FCN"],"derived_from_research_state_id":"rs-ep-1-001","transformation_id":"ct-ep-1-001"}
```

```json
{"action":"transform_worldview","source_research_state_id":"rs-ep-1-001","operator_id":"G2"}
```

All fields except `action` and `working_model` are optional for registration; both transform selectors are optional so a root/Child seed and an auto-selected operator remain legal.

- [ ] **Step 4: Implement the one-shot transformer**

Create `proposer/cognitive_transformer.py` with a fixed system contract:

```python
_SYSTEM = """Apply exactly one supplied cognitive operator to the supplied
research working model or episode seed. Preserve objective facts. Do not
generate implementation proposals. Do not declare the source model wrong.
Expose assumptions or boundaries targeted by the operator, offer alternative
framings, and end with questions that distinguish the framings. Return plain
text only."""
```

`CognitiveTransformer` receives the existing `ChatModel`, a `dict[str, Generator]`, an Episode seed string, and an optional suggested operator. Resolve the operator in this order: explicit `operator_id`, suggested operator, first generator not yet used in the current Episode. Call `model.complete()` once with `_SYSTEM`; return its free-text challenge and usage. Raise `ValueError` for an empty reply or unknown operator.

Add `select_one_generator()` in `simpleevo/generator.py`; it returns a single unused `Generator | None`. Keep `sample_generators()` for compatibility, but change Scheduler use only in Task 6.

- [ ] **Step 5: Dispatch registration/transformation without L2 writes**

Extend `ResearchTools.__init__()` with `node_id`, `episode_id`, and `cognitive_transformer`. Extend `execute()` with `working_state: WorkingState | None = None`.

For registration:

- assign the next deterministic ID;
- validate local `derived_from` and `transformation_id` references;
- allow inherited L2 source IDs only when they are present in the Episode seed’s allowed IDs;
- validate `experiment:`/`finding:` refs against `working_state.session_evidence` and source refs only after `__source_examined__` is present;
- append the immutable record to `working_state.research_states`;
- return only identity and acknowledgment, not an LLM-authored summary.

For transformation:

- resolve source text from a registered local state or the compiled Episode seed;
- call `CognitiveTransformer` once;
- assign and record a `CognitiveTransformation`;
- return `{ok, transformation_id, operator_id, challenge}`.

Change the Scientist loop call to:

```python
observation = tools.execute(
    action,
    deadline=deadline,
    working_state=state,
)
```

- [ ] **Step 6: Run focused tool tests and verify GREEN**

```bash
python -m pytest tests/proposer/test_research_state_tools.py tests/test_generator.py -q
```

Expected: all selected tests pass and FakeModel records exactly one call per transformation.

- [ ] **Step 7: Commit the cognitive-tool slice**

```bash
git add proposer/cognitive_transformer.py proposer/research_agent.py proposer/research_tools.py proposer/scientist.py simpleevo/generator.py tests/proposer/test_research_state_tools.py tests/test_generator.py
git commit -m "feat: add research state cognitive tools"
```

---

### Task 3: Proposal linkage and durable worker artifact

**Files:**

- Modify: `proposer/memory/models.py`
- Modify: `proposer/scientist.py`
- Modify: `proposer/orchestrator.py`
- Modify: `proposer/cli.py`
- Modify: `proposer/research_agent.py`
- Modify: `tests/proposer/test_research_state_tools.py`

**Interfaces:**

- `ResearchProposal` gains required `research_state_id: str` and `expectation: str`.
- `ScientistRound` and `EpisodeResult` expose tuples of local ResearchStates and CognitiveTransformations.
- Worker `result.json` gains top-level result arrays `research_states` and `transformations`; Proposal rationale carries `expectation` and `material_difference`.

- [ ] **Step 1: Write failing Proposal linkage tests**

Add tests asserting:

```python
def test_submit_proposal_requires_registered_research_state():
    state = WorkingState()
    action = parse_response(json.dumps({
        "action": "submit_proposals",
        "proposals": [{
            "research_state_id": "rs-ep-1-999",
            "instruction": "Preserve event-level invariants across FCN calls.",
            "expectation": "FCN call-local time and total time both decrease.",
            "research_target": {
                "mode": "new",
                "question": "Does boundary-owned state cause repeated work?",
                "mechanisms": ["state-lifecycle"],
                "code_regions": ["OMILRECV2"],
            },
        }],
    }), proposal_slots=3)
    assert _validate_action_guard(state, [action], Path(".")) == "unknown_research_state"
```

Add a positive test that registers one state, submits two materially different Proposals referencing it, and verifies both are accepted. Add serialization assertions that worker output includes the state and transformation arrays and each Proposal’s `research_state_id`/`expectation`.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python -m pytest tests/proposer/test_research_state_tools.py -q
```

Expected: parser rejects the new Proposal fields or guard does not detect the unknown state.

- [ ] **Step 3: Extend Proposal parsing and the terminal guard**

Make `_parse_proposal()` require non-empty `research_state_id`, `instruction`, and `expectation`. Preserve `research_target`, `evidence_refs`, and optional `material_difference`.

In `_validate_action_guard()`, when the terminal action is `submit_proposals`, require every referenced state ID to exist in `WorkingState.research_states`. Do not restrict the number of Proposals per ResearchState. Keep the existing total `proposal_slots` ceiling.

- [ ] **Step 4: Propagate cognitive records through results**

Add fields to `ScientistRound` and `EpisodeResult`:

```python
research_states: tuple[ResearchState, ...] = ()
transformations: tuple[CognitiveTransformation, ...] = ()
```

In `make_result()`, preserve action order using the insertion order of the two `WorkingState` dictionaries. In `_result_to_dict()`, serialize both arrays with the shared conversion helpers. In `_proposal_to_dict()`, write:

```python
"research_state_id": proposal.research_state_id,
"rationale": {
    "research_target": target,
    "expectation": proposal.expectation,
    "material_difference": proposal.material_difference,
},
```

The worker still attaches only Scheduler-reserved Proposal IDs; ResearchState and Transformation IDs originate in the host tool runtime, not model output.

- [ ] **Step 5: Include cognitive records in trace/telemetry**

Add `research_states_registered`, `transformations_requested`, and `proposed_research_states` counts to `_build_telemetry()`. Add registered IDs and transformation IDs to `_build_trace()` without duplicating full working-model text into the trace.

- [ ] **Step 6: Run focused tests and verify GREEN**

```bash
python -m pytest tests/proposer/test_research_state_tools.py tests/jobs/test_envelope.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the worker-artifact slice**

```bash
git add proposer/memory/models.py proposer/scientist.py proposer/orchestrator.py proposer/cli.py proposer/research_agent.py tests/proposer/test_research_state_tools.py
git commit -m "feat: link proposals to research states"
```

---

### Task 4: Atomic Scheduler ingest of ResearchState, Transformation, and Proposal

**Files:**

- Modify: `simpleevo/db/store.py`
- Modify: `simpleevo/scheduler/loop.py`
- Modify: `simpleevo/db/queries.py`
- Modify: `tests/db/test_research_state_store.py`
- Modify: `tests/test_scheduler_attempts.py`
- Modify: `tests/test_integration.py`

**Interfaces:**

- Produces `ResearchStore.publish_research_batch(...) -> list[Proposal]`.
- A successful proposer result persists transformations, states, and Proposals in one SQLite transaction, including abstentions that register states but submit no Proposal.
- A malformed reference rolls back the whole batch and leaves the allocation open only when treated as a worker/protocol failure.

- [ ] **Step 1: Write failing atomicity and ownership tests**

Add tests for a valid state plus two Proposals, state-only abstention, unknown transformation, cross-Node state, duplicate IDs, forged Proposal reservation, and full rollback:

```python
def test_publish_research_batch_is_atomic(store: ResearchStore):
    node, episode = _node_and_episode(store)
    with pytest.raises(ValueError, match="unknown research_state_id"):
        store.publish_research_batch(
            node_id=node.node_id,
            episode_id=episode.episode_id,
            transformations=[],
            research_states=[],
            proposals=[{
                "proposal_id": "p-1",
                "research_state_id": "rs-missing",
                "instruction": "try X",
                "rationale": {"expectation": "metric improves"},
            }],
            reserved_proposal_ids=("p-1",),
        )
    assert ResearchQueries(store.path).queued_proposals() == []
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/db/test_research_state_store.py tests/test_scheduler_attempts.py tests/test_integration.py -q
```

Expected: failure because `publish_research_batch()` and cognitive result ingestion do not exist.

- [ ] **Step 3: Implement one transaction for the research batch**

Implement `publish_research_batch()` by:

1. validating Node/Episode ownership;
2. validating host-generated IDs begin with `ct-{episode_id}-` / `rs-{episode_id}-` for local records;
3. validating source/derived references against existing L2 rows or incoming IDs;
4. inserting transformations;
5. inserting ResearchStates;
6. requiring every new Proposal’s `research_state_id` to resolve and belong to the same Node/Episode;
7. validating Scheduler-reserved Proposal IDs;
8. inserting Proposals and updating Episode activity;
9. committing only after all checks pass.

Keep `publish_proposals()` as a legacy wrapper for tests/tools that intentionally create pre-feature rows; all Scheduler worker ingest must use `publish_research_batch()`.

- [ ] **Step 4: Switch Scheduler result ingest to the atomic batch**

In `_ingest_proposer_result()`, read `transformations` and `research_states` even when `proposals` is empty. Call `publish_research_batch()` before `deallocate_proposer()`. Treat invalid cognitive payload as a failed proposer attempt, archive the bad result, and leave no partial L2 rows.

Update integration fixtures to emit one ResearchState and make the Proposal reference it.

- [ ] **Step 5: Run focused ingest tests and verify GREEN**

```bash
python -m pytest tests/db/test_research_state_store.py tests/test_scheduler_attempts.py tests/test_integration.py -q
```

Expected: all selected tests pass; the integration DB contains one ResearchState linked to the queued/completed Proposal.

- [ ] **Step 6: Commit the atomic-ingest slice**

```bash
git add simpleevo/db/store.py simpleevo/db/queries.py simpleevo/scheduler/loop.py tests/db/test_research_state_store.py tests/test_scheduler_attempts.py tests/test_integration.py
git commit -m "feat: ingest research state batches atomically"
```

---

### Task 5: Proposal-specific Child ResearchStateSeed handoff

**Files:**

- Create: `tests/test_research_state_seed.py`
- Modify: `proposer/context.py`
- Modify: `proposer/orchestrator.py`
- Modify: `proposer/cli.py`
- Modify: `proposer/scientist.py`
- Modify: `simpleevo/scheduler/loop.py`
- Modify: `tests/scheduler/test_frontier_persistence.py`
- Modify: `tests/proposer/test_scientist_session.py`

**Interfaces:**

- Produces `Scheduler._research_state_seed_for(node: Node) -> dict[str, Any]`; this is a small payload assembler, not a domain service or Evidence Compiler.
- Scheduler payload key becomes `research_state_seed`; `world_transition` remains accepted only as a legacy fallback.
- Child episodes do not hot-copy the full parent session when an originating ResearchState seed exists.

- [ ] **Step 1: Write failing seed-assembly tests**

Test root and Child behavior:

```python
@pytest.fixture
def store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path / "simpleevo.db")


def _scheduler(store: ResearchStore) -> Scheduler:
    return Scheduler(
        store,
        store.path.parent,
        SchedulerConfig(
            max_proposer_inflight=0,
            max_experiment_inflight=0,
            poll_seconds=0.0,
        ),
    )


def _seed_root(store: ResearchStore) -> Node:
    with store.transaction() as tx:
        return tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )


def _seed_completed_research_path(store: ResearchStore) -> Node:
    root = _seed_root(store)
    with store.transaction() as tx:
        episode = tx.create_episode(node_id=root.node_id)
        tx.create_research_state(ResearchState(
            research_state_id="rs-ep-1-001",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            derived_from_research_state_id=None,
            transformation_id=None,
            working_model="The boundary loses reusable state.",
            evidence_refs=("source:src/fcn.cc:FCN",),
            created_at=1.0,
        ))
        tx.create_proposal(Proposal(
            proposal_id="proposal-1",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            instruction="Preserve reusable state across FCN calls.",
            rationale={"expectation": "total_ms decreases"},
            status="running",
            created_at=2.0,
            research_state_id="rs-ep-1-001",
        ))
        experiment = tx.create_experiment(
            experiment_id="experiment-1",
            proposal_id="proposal-1",
            parent_node_id=root.node_id,
        )
    child = store.ingest_experiment_result(
        experiment_id=experiment.experiment_id,
        result_sha="sha-child",
        metrics={"total_ms": 90.0},
        gate_result=GateDecision({}, True),
        status="completed",
        changed_paths=("src/fcn.cc",),
    )
    assert child is not None
    return child


def test_child_seed_joins_state_expectation_and_outcome(store):
    child = _seed_completed_research_path(store)
    scheduler = _scheduler(store)
    seed = scheduler._research_state_seed_for(child)
    assert seed["child_node"]["node_id"] == child.node_id
    assert seed["originating_research_state"]["working_model"] == (
        "The boundary loses reusable state."
    )
    assert seed["proposal"]["expectation"] == "total_ms decreases"
    assert seed["experiment"]["metrics"] == {"total_ms": 90.0}
    assert "interpretation" not in seed


def test_root_has_no_research_state_seed(store):
    root = _seed_root(store)
    assert _scheduler(store)._research_state_seed_for(root) == {}


def test_seed_pack_separates_judgment_from_harness_facts(store):
    seed = _scheduler(store)._research_state_seed_for(
        _seed_completed_research_path(store)
    )
    text = build_research_state_seed_pack(seed)
    assert "Originating working model — Scientist judgment" in text
    assert "Experiment outcome — authoritative Harness facts" in text
    assert "Re-ground in the current Child world" in text
```

Use imports from `pytest`, `pathlib`, `simpleevo.db.store`, `simpleevo.research_state`, `simpleevo.scheduler.loop`, and `proposer.context` exactly as required by the snippet.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/test_research_state_seed.py tests/proposer/test_scientist_session.py tests/scheduler/test_frontier_persistence.py -q
```

Expected: failure because `_research_state_seed_for()` and the new payload are absent.

- [ ] **Step 3: Assemble the seed in the existing Scheduler path**

Add `_research_state_seed_for()` next to `_world_transition_for()` in `simpleevo/scheduler/loop.py`. It follows only foreign-key/identity links:

```text
Node.experiment_id
→ Experiment.proposal_id
→ Proposal.research_state_id
→ ResearchState
```

Return `{}` for a root Node or legacy chain without `research_state_id`. Otherwise return plain dicts containing Child Node identity/metrics/gate, originating state identity/working model/evidence refs, Proposal instruction/expectation/material difference, and Experiment identity/metrics/gate/changed paths/parent metrics. Reuse `_world_transition_for()` for experiment facts. Do not create a new production module, call a model, resolve evidence refs, summarize, rank, verify claims, or emit epistemic status.

- [ ] **Step 4: Render and inject the seed**

Add `build_research_state_seed_pack(seed)` in `proposer/context.py`. It must include these labels verbatim:

```text
Originating working model — Scientist judgment, not an established fact:
Experiment outcome — authoritative Harness facts:
Re-ground in the current Child world before registering a revised ResearchState.
```

Pass the seed from Scheduler payload → CLI → Orchestrator → Scientist. Use the rendered seed as the transformation fallback source when `source_research_state_id` is omitted.

- [ ] **Step 5: Stop Child session contamination**

In `proposer/cli.py`, skip `_inherit_parent_session()` whenever `research_state_seed` contains `originating_research_state`. A crash retry still reuses the current Episode directory. Preserve same-Node reseed session inheritance for this MVP because it is a separate policy from Child branch isolation.

Update Child Episode tests: `inherited_from_episode_id` remains provenance, but startup context comes from the Proposal-specific seed and does not copy sibling session content.

- [ ] **Step 6: Run focused seed tests and verify GREEN**

```bash
python -m pytest tests/test_research_state_seed.py tests/proposer/test_scientist_session.py tests/scheduler/test_frontier_persistence.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the Child-state handoff slice**

```bash
git add simpleevo/scheduler/loop.py proposer/context.py proposer/orchestrator.py proposer/cli.py proposer/scientist.py tests/test_research_state_seed.py tests/proposer/test_scientist_session.py tests/scheduler/test_frontier_persistence.py
git commit -m "feat: assemble proposal-specific child research seeds"
```

---

### Task 6: Node lifetime Proposal budget and single-generator reseed

**Files:**

- Create: `tests/test_node_proposal_budget.py`
- Modify: `simpleevo/config.py`
- Modify: `simpleevo/db/store.py`
- Modify: `simpleevo/scheduler/loop.py`
- Modify: `simpleevo/generator.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_scheduler_reseed.py`
- Modify: `examples/tiny_algo_opt/task.yaml`
- Modify: `examples/tiny_algo_opt/task.condor.yaml`
- Modify: `examples/xsbench_opt/task.yaml`
- Modify: `examples/xsbench_opt/task-fractal.yaml`
- Modify: `examples/omilrec_opt/task.yaml`

**Interfaces:**

- Adds `EvolutionConfig.max_proposals_per_node: int = 9` for backward-compatible default capacity (`proposal_slots=3 × max_research_per_node=3`).
- `ResearchStore.allocate_proposer(..., max_proposals_per_node: int) -> ProposerAllocation | None` atomically reserves only remaining Node capacity.
- Reseed variation stores one generator ID, never `G1+G2`.

- [ ] **Step 1: Write failing budget and concurrency tests**

Cover published counts, open reservations, unused reservation release, concurrent allocations, and exhaustion:

```python
def test_node_budget_counts_published_and_open_reservations(store):
    node, first, second = _node_with_two_fresh_episodes(store)
    a1 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=first.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    a2 = store.allocate_proposer(
        node_id=node.node_id,
        episode_id=second.episode_id,
        proposal_slots=3,
        max_proposals_per_node=4,
    )
    assert len(a1.reserved_proposal_ids) == 3
    assert len(a2.reserved_proposal_ids) == 1
```

Add a test that closing `a1` with one produced Proposal releases two unused reservations, allowing a later Episode to reserve them. Add a test that Scheduler submits no proposer when remaining capacity is zero.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m pytest tests/test_node_proposal_budget.py tests/test_config.py tests/test_scheduler_reseed.py -q
```

Expected: signature/config failures.

- [ ] **Step 3: Add config and atomic reservation accounting**

Add `max_proposals_per_node` to `EvolutionConfig`, `to_dict()`, and `from_dict()`. In one Store transaction compute:

```sql
SELECT COUNT(*) FROM proposals WHERE node_id = ?;
SELECT reserved_proposal_ids FROM proposer_allocations
WHERE node_id = ? AND finished_at IS NULL;
```

Reserve `min(proposal_slots, max_proposals_per_node - published - open_reserved)` IDs. Return `None` when remaining capacity is zero; do not force at least one reservation. Scheduler skips `None` and does not record an Attempt.

Keep `max_research_per_node` unchanged so repeated abstentions cannot consume unbounded Proposer compute.

- [ ] **Step 4: Make reseed select exactly one suggested generator**

Change `_variation_for()` to use `select_one_generator()` and store one ID. Scheduler payload sends:

```python
"generator_basis": [
    {"id": item.id, "name": item.name, "description": item.description}
    for item in self._generator_basis_or_load()
],
"suggested_operator_id": episode.variation_operator,
```

Remove `G1+G2` splitting and `_variation_hints()` injection. The tool, not a standing prompt hint, applies the generator and records whether it was adopted.

- [ ] **Step 5: Update example budgets explicitly**

Set `max_proposals_per_node` to the prior intended lifetime capacity in each example. Use `9` for configurations with `proposal_slots: 3` and `max_research_per_node: 3`; use `3` for the fractal configuration with `max_research_per_node: 1`. Do not change unrelated experiment parameters.

- [ ] **Step 6: Run focused budget/reseed tests and verify GREEN**

```bash
python -m pytest tests/test_node_proposal_budget.py tests/test_config.py tests/test_scheduler_reseed.py tests/test_example_config.py tests/test_omilrec_example_config.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit the budget slice**

```bash
git add simpleevo/config.py simpleevo/db/store.py simpleevo/scheduler/loop.py simpleevo/generator.py tests/test_node_proposal_budget.py tests/test_config.py tests/test_scheduler_reseed.py examples/tiny_algo_opt/task.yaml examples/tiny_algo_opt/task.condor.yaml examples/xsbench_opt/task.yaml examples/xsbench_opt/task-fractal.yaml examples/omilrec_opt/task.yaml
git commit -m "feat: enforce node proposal budgets"
```

---

### Task 7: Prompt semantics, width telemetry, and end-to-end verification

**Files:**

- Create: `tests/scheduler/test_research_state_telemetry.py`
- Modify: `proposer/prompts/proposer.md`
- Modify: `proposer/scientist.py`
- Modify: `simpleevo/scheduler/telemetry.py`
- Modify: `simpleevo/db/queries.py`
- Modify: `tests/test_integration.py`
- Modify: `docs/design/evolution_for_research_state.md`

**Interfaces:**

- Telemetry writes `telemetry/research_state_width.jsonl` with Node-local registered-state count, proposed-state count, Proposal count, and maximum per-state Proposal concentration.
- Prompt tells the Scientist to own its working models, use transformation as optional mentor consultation, and treat one-state/one-Proposal only as breadth guidance.

- [ ] **Step 1: Write failing telemetry and end-to-end assertions**

Add a telemetry test with three states and five Proposals distributed `3/1/1`:

```python
record = recorder.record(step=4, frontier_size=1, queries=queries)
width = record.research_state_width[0]
assert width == {
    "node_id": node.node_id,
    "registered_states": 3,
    "proposed_states": 3,
    "total_proposals": 5,
    "max_proposals_per_state": 3,
}
```

Extend `test_scheduler_closes_proposer_experiment_loop()` to assert:

- ResearchState and Transformation rows are ingested;
- Proposal references the ResearchState;
- Experiment creates the Child Node;
- `Scheduler._research_state_seed_for(child)` returns the originating working model and actual outcome;
- a second Proposal from the same state remains legal.

- [ ] **Step 2: Run the tests and verify RED**

```bash
python -m pytest tests/scheduler/test_research_state_telemetry.py tests/test_integration.py -q
```

Expected: missing telemetry field/file or incomplete integration payload.

- [ ] **Step 3: Add deterministic width telemetry**

Add a `ResearchQueries.research_state_width()` aggregate using SQL joins only. Extend `StepTelemetry` with `research_state_width: list[dict[str, Any]]`; append one JSONL row per Node per Scheduler step. Do not compare `working_model` text or call a model.

- [ ] **Step 4: Update Scientist semantics without adding a workflow**

Update `_PROTOCOL_BLOCK` and `proposer/prompts/proposer.md` to state:

```text
A ResearchState is your revisable working model of the current world, not a
Harness fact and not a form to complete. Register it when it is useful to make
the understanding behind an experiment explicit. transform_worldview is an
optional, stateless mentor consultation: it challenges a framing but cannot
register a state or submit a Proposal for you. Prefer breadth across viable
ResearchStates before spending multiple Proposals under one state, but submit
every materially distinct experiment worth its cost and never pad a quota.
```

Do not prescribe a mandatory transform → register → submit sequence.

- [ ] **Step 5: Document the accepted generator mechanics**

Add a subsection to `docs/design/evolution_for_research_state.md` documenting:

```text
ResearchState or Child seed → exact transformation input
Generator → one cognitive operation
Stateless Transformer → challenge only
Scientist → acceptance/rejection and new working model
Harness → provenance and budget
```

State that multiple generators compose through multiple explicit transformation records, not one combined prompt hint. Keep Chain-of-Evidence / Evidence Compiler explicitly out of this implementation.

- [ ] **Step 6: Run the complete relevant suite**

```bash
python -m pytest tests/db tests/proposer tests/scheduler tests/test_generator.py tests/test_scheduler_reseed.py tests/test_node_proposal_budget.py tests/test_research_state_seed.py tests/test_scheduler_attempts.py tests/test_integration.py tests/test_config.py tests/test_example_config.py tests/test_omilrec_example_config.py -q
```

Expected: all selected tests pass with zero failures.

- [ ] **Step 7: Run full regression and syntax verification**

```bash
python -m pytest -q
python -m compileall -q simpleevo proposer experiment tests
git diff --check
```

Expected: pytest exits 0, compileall exits 0, and `git diff --check` emits no output.

- [ ] **Step 8: Commit the final integration slice**

```bash
git add proposer/prompts/proposer.md proposer/scientist.py simpleevo/scheduler/telemetry.py simpleevo/db/queries.py tests/scheduler/test_research_state_telemetry.py tests/test_integration.py docs/design/evolution_for_research_state.md
git commit -m "feat: complete research state evolution flow"
```

---

## Final Review Checklist

- [ ] `git branch --show-current` prints `research_state_evolve`.
- [ ] `git log --oneline --grep='^feat:' c79caa9..HEAD` shows the seven implementation task commits in dependency order.
- [ ] Every newly ingested Proposal has a non-null `research_state_id`; legacy rows remain readable.
- [ ] A state-only abstention persists ResearchStates and Transformations without creating Proposals.
- [ ] Invalid cross-Node/cross-Episode references roll back the entire proposer batch.
- [ ] `transform_worldview` makes one stateless model call with one generator and never registers a state itself.
- [ ] Child startup contains only its originating ResearchState plus corresponding Proposal/Experiment facts, not sibling state/session content.
- [ ] No Evidence Compiler, claim model, verification layer, or proof pack is added; `evidence_refs` remains optional provenance.
- [ ] ResearchStateSeed assembly is deterministic, follows only identity links, and contains no inferred support/contradiction/recommendation.
- [ ] Concurrent allocations cannot exceed `max_proposals_per_node`; unused reservations are released.
- [ ] Frontier remains Node/metrics based and no semantic diversity judge exists.
- [ ] Full pytest, compileall, and diff checks pass from fresh output.
