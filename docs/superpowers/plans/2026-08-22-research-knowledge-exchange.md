# Research Knowledge Exchange Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every Scientist discover world-scoped experiments across branches and deliberately inspect an experiment's attributed ResearchState without turning sibling interpretations into default context or authoritative facts.

**Architecture:** Extend the existing `L2MemoryService` with deterministic evidence and research-memo projections over the current SQLite records; do not add tables. Track explicit experiment inspection in the round-local `WorkingState`, require that state before exposing an originating ResearchState, and inject the existing low-semantic coverage pack into both cold and resumed Scientist contexts. Search remains an index only; only explicit experiment inspection makes `experiment:<id>` citable evidence.

**Tech Stack:** Python 3.9+, dataclasses, SQLite, pytest.

## Global Constraints

- Do not add a Knowledge table, Knowledge Object, author identity, confidence field, or belief-status state machine.
- Do not add `merge_branch`, `SynthesisProposal`, automatic synthesis, or any new proposal workflow.
- Do not change Frontier scoring, node survival, proposal budgets, or scheduler allocation.
- Experiment Ledger remains the only authoritative cross-branch fact source.
- Every evidence detail must expose its source world; Research Memo output must be marked `SUBJECTIVE_RESEARCH_MEMO`.
- Search results must not expose Proposal text or ResearchState text and must not rank by objective gain, popularity, or citation count.
- A sibling ResearchState is available only after the same Episode explicitly inspects its concrete Experiment.
- A search hit alone is not citable evidence; `inspect_experiment` makes `experiment:<id>` visible for `ResearchState.evidence_refs`.
- Every Proposal continues to reference exactly one ResearchState; a ResearchState may cite multiple inspected experiments.
- Reuse current Node, Experiment, Proposal, Episode, ResearchState, `WorkingState`, and L2 query identities; add no schema migration.
- Preserve the user's untracked `docs/chat/2026.8.22.2.57.gpt聊知识共享.md` and never stage it.

---

## File Structure

- Modify `proposer/l2_memory.py`: render low-semantic cross-branch coverage, expose complete world-scoped experiment detail, and resolve an Experiment to its originating ResearchState.
- Modify `proposer/research_agent.py`: remember explicitly inspected experiments and register only inspected experiment detail as citable evidence.
- Modify `proposer/research_tools.py`: declare and execute `inspect_originating_research_state`, enforcing the inspect-first gate.
- Modify `proposer/scientist.py`: parse/fingerprint/summarize both inspection tools and inject coverage on cold starts as well as resumes.
- Modify `proposer/prompts/proposer.md`: state the evidence-index, explicit-inspection, and subjective-memo semantics in the Scientist charter.
- Modify `tests/proposer/test_l2_memory.py`: cover world-scoped evidence, cross-branch coverage, and deterministic memo resolution.
- Modify `tests/proposer/test_research_state_tools.py`: cover protocol parsing, inspect-first access, evidence visibility, and multi-experiment ResearchState synthesis.
- Create `tests/proposer/test_knowledge_exchange_context.py`: cover cold/resumed coverage message construction without running an LLM.

---

### Task 1: World-scoped Shared Evidence View

**Files:**
- Modify: `proposer/l2_memory.py`
- Modify: `tests/proposer/test_l2_memory.py`

**Interfaces:**
- Consumes: existing `ResearchQueries.list_experiments()`, `get_experiment()`, `get_proposal()`, and `get_node()`.
- Produces: `L2MemoryService.inspect_experiment(experiment_id: str) -> dict` with `source_world`, `intervention`, recorded `condition`, and `observation`; `L2MemoryService.build_coverage_pack(current_round: int = 0) -> str` with aggregate coverage only.

- [ ] **Step 1: Add a reusable L2 fixture containing two sibling experiments**

Add this helper to `tests/proposer/test_l2_memory.py` and import `ResearchState`:

```python
from simpleevo.research_state import ResearchState


def _seed_sibling_experiments(store: ResearchStore) -> dict[str, str]:
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={"total_ms": 100.0},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        state = tx.create_research_state(ResearchState(
            research_state_id="rs-source-001",
            node_id=root.node_id,
            episode_id=episode.episode_id,
            derived_from_research_state_id=None,
            transformation_id=None,
            working_model="Lookup traversal and layout may be coupled.",
            evidence_refs=("source:src/lookup.c:lookup",),
            created_at=1.0,
        ))
        proposal_a = tx.create_proposal(type("P", (), {
            "proposal_id": "p-layout",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": state.research_state_id,
            "instruction": "change lookup layout from AoS to SoA",
            "rationale": {"expectation": "total_ms decreases"},
            "status": "queued",
            "created_at": 2.0,
        })())
        proposal_b = tx.create_proposal(type("P", (), {
            "proposal_id": "p-cache",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": state.research_state_id,
            "instruction": "cache repeated lookup coefficients",
            "rationale": {"expectation": "total_ms decreases"},
            "status": "queued",
            "created_at": 3.0,
        })())
        exp_a = tx.create_experiment(
            experiment_id="exp-layout",
            proposal_id=proposal_a.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
        tx.update_experiment_result(
            experiment_id=exp_a.experiment_id,
            result_sha="sha-layout",
            metrics={"total_ms": 80.0},
            gate_result=GateDecision(
                {"CORRECT": GateResult(True, "")}, True,
            ),
            status="completed",
            changed_paths=("src/layout.c",),
        )
        child_a = tx.create_node(
            parent_node_id=root.node_id,
            experiment_id=exp_a.experiment_id,
            sha="sha-layout",
            metrics={"total_ms": 80.0},
            gate_result=GateDecision({}, True),
            depth=1,
            status="active",
        )
        tx.link_experiment_child(exp_a.experiment_id, child_a.node_id)
        exp_b = tx.create_experiment(
            experiment_id="exp-cache",
            proposal_id=proposal_b.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )
        tx.update_experiment_result(
            experiment_id=exp_b.experiment_id,
            result_sha="sha-cache",
            metrics={"total_ms": 95.0},
            gate_result=GateDecision(
                {"CORRECT": GateResult(False, "mismatch")}, False,
            ),
            status="gate_rejected",
            changed_paths=("src/cache.c",),
        )
    return {
        "root_node_id": root.node_id,
        "episode_id": episode.episode_id,
        "research_state_id": state.research_state_id,
    }
```

- [ ] **Step 2: Write failing evidence-view tests**

Replace the existing `test_inspect_experiment` with the first test below, then append the remaining tests to `tests/proposer/test_l2_memory.py`:

```python
def test_inspect_experiment_is_world_scoped(store: ResearchStore):
    ids = _seed_sibling_experiments(store)
    detail = L2MemoryService(store.path.parent).inspect_experiment("exp-layout")

    assert detail["experiment_id"] == "exp-layout"
    assert detail["source_world"] == {
        "node_id": ids["root_node_id"],
        "sha": "sha-root",
        "metrics": {"total_ms": 100.0},
    }
    assert detail["intervention"] == {
        "proposal_id": "p-layout",
        "instruction": "change lookup layout from AoS to SoA",
        "changed_paths": ["src/layout.c"],
    }
    assert detail["observation"]["metrics"] == {"total_ms": 80.0}
    assert detail["condition"] == {"recorded_gates": ["CORRECT"]}
    assert detail["observation"]["gate"]["passed"] is True


def test_search_experiments_is_global_but_returns_no_direction_text(
    store: ResearchStore,
):
    _seed_sibling_experiments(store)
    result = L2MemoryService(store.path.parent).search_experiments(
        "lookup", limit=10, buckets=False,
    )

    assert {row["experiment_id"] for row in result["results"]} == {
        "exp-layout", "exp-cache",
    }
    assert all("instruction" not in row for row in result["results"])
    assert all("working_model" not in row for row in result["results"])


def test_coverage_pack_aggregates_regions_and_outcomes_without_recommendation(
    store: ResearchStore,
):
    _seed_sibling_experiments(store)
    text = L2MemoryService(store.path.parent).build_coverage_pack()

    assert "src/layout.c: experiments=1 gate_passed=1 gate_failed=0" in text
    assert "src/cache.c: experiments=1 gate_passed=0 gate_failed=1" in text
    assert "promising" not in text.lower()
    assert "best" not in text.lower()
    assert "change lookup layout" not in text
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: failures because `inspect_experiment` has no `source_world`, `intervention`, `condition`, or `observation`, and the coverage pack lists Nodes rather than aggregate regions.

- [ ] **Step 4: Implement the minimal world-scoped projections**

In `proposer/l2_memory.py`, replace `build_coverage_pack`, extend `inspect_experiment`, and keep search result rows free of direction text:

```python
    def build_coverage_pack(self, *, current_round: int = 0) -> str:
        """Return aggregate cross-branch coverage without direction text."""
        by_path: dict[str, dict[str, int]] = {}
        for experiment in self.queries.list_experiments():
            for path in experiment.changed_paths:
                row = by_path.setdefault(
                    path,
                    {"experiments": 0, "gate_passed": 0, "gate_failed": 0},
                )
                row["experiments"] += 1
                key = "gate_passed" if experiment.gate_result.passed else "gate_failed"
                row[key] += 1
        lines = [
            "Coverage map — global experiment coverage, not a direction ranking:",
        ]
        for path in sorted(by_path):
            row = by_path[path]
            lines.append(
                f"- {path}: experiments={row['experiments']} "
                f"gate_passed={row['gate_passed']} "
                f"gate_failed={row['gate_failed']}"
            )
        if not by_path:
            lines.append("- no completed experiment paths recorded")
        return "\n".join(lines)

    def inspect_experiment(self, experiment_id: str) -> dict:
        """Return one world-scoped experiment record."""
        experiment = self.queries.get_experiment(experiment_id)
        if experiment is None:
            return {"ok": False, "error": f"experiment not found: {experiment_id}"}
        proposal = self.queries.get_proposal(experiment.proposal_id)
        parent = self.queries.get_node(experiment.parent_node_id)
        child = (
            self.queries.get_node(experiment.child_node_id)
            if experiment.child_node_id else None
        )
        return {
            "experiment_id": experiment.experiment_id,
            "source_world": {
                "node_id": experiment.parent_node_id,
                "sha": parent.sha if parent else None,
                "metrics": dict(parent.metrics) if parent else {},
            },
            "intervention": {
                "proposal_id": experiment.proposal_id,
                "instruction": proposal.instruction if proposal else None,
                "changed_paths": list(experiment.changed_paths),
            },
            "condition": {
                "recorded_gates": sorted(experiment.gate_result.results),
            },
            "observation": {
                "result_sha": experiment.result_sha,
                "child_node_id": experiment.child_node_id,
                "child_sha": child.sha if child else None,
                "metrics": dict(experiment.metrics),
                "gate": {
                    "passed": experiment.gate_result.passed,
                    "results": {
                        name: {"passed": result.passed, "detail": result.detail}
                        for name, result in experiment.gate_result.results.items()
                    },
                },
                "status": experiment.status,
            },
        }
```

In `search_experiments`, retain Proposal instruction only in the internal search haystack. For the two-fixture test, replace the haystack construction with:

```python
            haystack = " ".join([
                proposal.instruction if proposal else "",
                *list(exp.changed_paths),
            ]).lower()
```

No search row may return that instruction.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 6: Commit the evidence view**

```bash
git add proposer/l2_memory.py tests/proposer/test_l2_memory.py
git commit -m "feat: expose world-scoped experiment evidence"
```

---

### Task 2: Deterministic Research Memo Projection

**Files:**
- Modify: `proposer/l2_memory.py`
- Modify: `tests/proposer/test_l2_memory.py`

**Interfaces:**
- Consumes: `Experiment.proposal_id`, `Proposal.research_state_id`, `ResearchState.node_id`, and existing L2 getters.
- Produces: `L2MemoryService.inspect_originating_research_state(experiment_id: str) -> dict`.

- [ ] **Step 1: Write failing memo projection tests**

Append to `tests/proposer/test_l2_memory.py`:

```python
def test_inspect_originating_research_state_returns_attributed_subjective_memo(
    store: ResearchStore,
):
    ids = _seed_sibling_experiments(store)
    memo = L2MemoryService(
        store.path.parent,
    ).inspect_originating_research_state("exp-layout")

    assert memo == {
        "ok": True,
        "kind": "SUBJECTIVE_RESEARCH_MEMO",
        "experiment_id": "exp-layout",
        "research_state_id": ids["research_state_id"],
        "source_episode_id": ids["episode_id"],
        "source_world": {
            "node_id": ids["root_node_id"],
            "sha": "sha-root",
        },
        "working_model": "Lookup traversal and layout may be coupled.",
        "evidence_refs": ["source:src/lookup.c:lookup"],
        "derived_from_research_state_id": None,
        "transformation_id": None,
    }


def test_inspect_originating_research_state_reports_unavailable_without_state(
    store: ResearchStore,
):
    with store.transaction() as tx:
        root = tx.create_node(
            parent_node_id=None,
            experiment_id=None,
            sha="sha-root",
            metrics={},
            gate_result=GateDecision({}, True),
            depth=0,
            status="active",
        )
        episode = tx.create_episode(
            inherited_from_episode_id=None,
            node_id=root.node_id,
        )
        proposal = tx.create_proposal(type("P", (), {
            "proposal_id": "p-legacy",
            "node_id": root.node_id,
            "episode_id": episode.episode_id,
            "research_state_id": None,
            "instruction": "legacy experiment",
            "rationale": {},
            "status": "queued",
            "created_at": 1.0,
        })())
        tx.create_experiment(
            experiment_id="exp-legacy",
            proposal_id=proposal.proposal_id,
            parent_node_id=root.node_id,
            status="completed",
        )

    result = L2MemoryService(
        store.path.parent,
    ).inspect_originating_research_state("exp-legacy")
    assert result == {
        "ok": False,
        "error": "research memo unavailable for experiment: exp-legacy",
    }
```

- [ ] **Step 2: Run tests and verify the method is missing**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: FAIL with `AttributeError: 'L2MemoryService' object has no attribute 'inspect_originating_research_state'`.

- [ ] **Step 3: Implement the deterministic identity join**

Add to `L2MemoryService` in `proposer/l2_memory.py`:

```python
    def inspect_originating_research_state(self, experiment_id: str) -> dict:
        """Return the attributed memo behind one concrete experiment."""
        experiment = self.queries.get_experiment(experiment_id)
        if experiment is None:
            return {"ok": False, "error": f"experiment not found: {experiment_id}"}
        proposal = self.queries.get_proposal(experiment.proposal_id)
        if proposal is None:
            return {
                "ok": False,
                "error": f"proposal missing for experiment: {experiment_id}",
            }
        if not proposal.research_state_id:
            return {
                "ok": False,
                "error": f"research memo unavailable for experiment: {experiment_id}",
            }
        state = self.queries.get_research_state(proposal.research_state_id)
        if state is None:
            return {
                "ok": False,
                "error": f"research state missing: {proposal.research_state_id}",
            }
        source_node = self.queries.get_node(state.node_id)
        return {
            "ok": True,
            "kind": "SUBJECTIVE_RESEARCH_MEMO",
            "experiment_id": experiment_id,
            "research_state_id": state.research_state_id,
            "source_episode_id": state.episode_id,
            "source_world": {
                "node_id": state.node_id,
                "sha": source_node.sha if source_node else None,
            },
            "working_model": state.working_model,
            "evidence_refs": list(state.evidence_refs),
            "derived_from_research_state_id": state.derived_from_research_state_id,
            "transformation_id": state.transformation_id,
        }
```

Do not summarize or reinterpret `working_model`.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit the memo projection**

```bash
git add proposer/l2_memory.py tests/proposer/test_l2_memory.py
git commit -m "feat: project attributed research memos"
```

---

### Task 3: Inspect-first Tool Protocol and Evidence Visibility

**Files:**
- Modify: `proposer/research_agent.py`
- Modify: `proposer/research_tools.py`
- Modify: `proposer/scientist.py`
- Modify: `tests/proposer/test_research_state_tools.py`

**Interfaces:**
- Consumes: `L2MemoryService.inspect_experiment(str) -> dict` and `inspect_originating_research_state(str) -> dict`.
- Produces: action `inspect_originating_research_state`; `WorkingState.inspected_experiment_ids: set[str]`; citable refs only after explicit inspection.

- [ ] **Step 1: Upgrade the tool-test memory fake**

Replace `FakeMemory` in `tests/proposer/test_research_state_tools.py` with:

```python
class FakeMemory:
    def inspect_experiment(self, experiment_id: str) -> dict:
        if experiment_id == "exp-missing":
            return {"ok": False, "error": "experiment not found: exp-missing"}
        return {
            "experiment_id": experiment_id,
            "source_world": {"node_id": "node-sibling", "sha": "sha-sibling"},
            "intervention": {"proposal_id": "p-sibling", "changed_paths": []},
            "observation": {"metrics": {"total_ms": 80.0}},
        }

    def inspect_originating_research_state(self, experiment_id: str) -> dict:
        return {
            "ok": True,
            "kind": "SUBJECTIVE_RESEARCH_MEMO",
            "experiment_id": experiment_id,
            "research_state_id": "rs-sibling-001",
            "source_episode_id": "ep-sibling",
            "source_world": {"node_id": "node-sibling", "sha": "sha-sibling"},
            "working_model": "Sibling interpretation, not a fact.",
            "evidence_refs": [],
            "derived_from_research_state_id": None,
            "transformation_id": None,
        }
```

- [ ] **Step 2: Write failing parser and access-gate tests**

Add imports and tests in `tests/proposer/test_research_state_tools.py`:

```python
from proposer.research_agent import _register_evidence


def test_parser_accepts_experiment_and_memo_inspection_actions():
    experiment = parse_response(
        '{"action":"inspect_experiment","experiment_id":"exp-1"}',
        proposal_slots=1,
    )
    memo = parse_response(
        '{"action":"inspect_originating_research_state",'
        '"experiment_id":"exp-1"}',
        proposal_slots=1,
    )
    assert experiment == {"action": "inspect_experiment", "experiment_id": "exp-1"}
    assert memo == {
        "action": "inspect_originating_research_state",
        "experiment_id": "exp-1",
    }


def test_research_memo_requires_explicit_experiment_inspection(tmp_path):
    state = WorkingState()
    result = _tools(tmp_path).execute(
        {
            "action": "inspect_originating_research_state",
            "experiment_id": "exp-1",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert result == {
        "ok": False,
        "error": "inspect experiment before requesting its research memo: exp-1",
    }


def test_explicit_inspection_unlocks_memo_and_citable_evidence(tmp_path):
    state = WorkingState()
    tools = _tools(tmp_path)
    action = {"action": "inspect_experiment", "experiment_id": "exp-1"}
    observation = tools.execute(
        action,
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    _register_evidence(state, action, observation)

    memo = tools.execute(
        {
            "action": "inspect_originating_research_state",
            "experiment_id": "exp-1",
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert memo["ok"] is True
    assert memo["result"]["kind"] == "SUBJECTIVE_RESEARCH_MEMO"
    assert state.inspected_experiment_ids == {"exp-1"}
    assert "experiment:exp-1" in state.session_evidence


def test_search_hit_does_not_become_citable_evidence():
    state = WorkingState()
    _register_evidence(
        state,
        {"action": "search_experiments", "query": "lookup"},
        {
            "ok": True,
            "result": {
                "relevant": [{"experiment_id": "exp-1"}],
                "contrasting": [],
                "diverse": [],
            },
        },
    )
    assert state.session_evidence == set()
    assert state.inspected_experiment_ids == set()


def test_research_state_can_cite_two_explicitly_inspected_experiments(tmp_path):
    state = WorkingState()
    tools = _tools(tmp_path)
    for experiment_id in ("exp-a", "exp-b"):
        action = {"action": "inspect_experiment", "experiment_id": experiment_id}
        observation = tools.execute(
            action,
            deadline=time.monotonic() + 10,
            working_state=state,
        )
        _register_evidence(state, action, observation)

    registered = tools.execute(
        {
            "action": "register_research_state",
            "working_model": "A and B may be compatible, but need one experiment.",
            "evidence_refs": ["experiment:exp-a", "experiment:exp-b"],
        },
        deadline=time.monotonic() + 10,
        working_state=state,
    )
    assert registered["ok"] is True
    record = state.research_states[registered["research_state_id"]]
    assert record.evidence_refs == ("experiment:exp-a", "experiment:exp-b")
```

- [ ] **Step 3: Run tests and verify protocol/gate failures**

Run:

```bash
python -m pytest tests/proposer/test_research_state_tools.py -q
```

Expected: parser rejects both missing action branches, `WorkingState` lacks `inspected_experiment_ids`, and memo execution is unsupported.

- [ ] **Step 4: Track explicit inspection separately from search discovery**

In `proposer/research_agent.py`, add the field:

```python
    inspected_experiment_ids: set[str] = field(default_factory=set)
```

Update `_register_evidence`:

```python
    elif name == "inspect_experiment":
        eid = (result or {}).get("experiment_id")
        if eid:
            ref = f"experiment:{eid}"
            state.inspected_experiment_ids.add(eid)
            state.session_evidence.add(ref)
            state.new_evidence.add(ref)
```

Delete the existing `search_experiments` branch that adds every search hit to
`session_evidence`. Remove `_iter_experiment_hits` if it becomes unused.

- [ ] **Step 5: Declare and execute the memo tool**

In `proposer/research_tools.py`, add this `ResearchToolSpec` immediately after
`inspect_experiment`:

```python
    ResearchToolSpec(
        action="inspect_originating_research_state",
        schema=(
            '{"action":"inspect_originating_research_state",'
            '"experiment_id":"<id>"}'
        ),
        description=(
            "After you explicitly inspect one Experiment, optionally read the "
            "ResearchState that originated it. The result is an attributed, "
            "world-scoped SUBJECTIVE_RESEARCH_MEMO, never a fact or instruction."
        ),
    ),
```

Add `"inspect_originating_research_state"` to `MEMORY_TOOL_ACTIONS` and add this
execution branch after `inspect_experiment`:

```python
            if name == "inspect_originating_research_state":
                state = self._require_cognitive_state(working_state)
                experiment_id = action["experiment_id"]
                if experiment_id not in state.inspected_experiment_ids:
                    raise ValueError(
                        "inspect experiment before requesting its research memo: "
                        f"{experiment_id}"
                    )
                return {
                    "ok": True,
                    "result": self.memory.inspect_originating_research_state(
                        experiment_id
                    ),
                }
```

- [ ] **Step 6: Add parser, fingerprint, and safe trace summaries**

In `_dispatch` in `proposer/scientist.py`, add one shared parser branch:

```python
    if name in {"inspect_experiment", "inspect_originating_research_state"}:
        _require_keys(action, {"action", "experiment_id"})
        experiment_id = action["experiment_id"]
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise ProposerError(f"{name}.experiment_id must be non-empty")
        return {"action": name, "experiment_id": experiment_id.strip()}
```

In `_fingerprint` in `proposer/research_agent.py`, use only the id, never memo text:

```python
    if name in {"inspect_experiment", "inspect_originating_research_state"}:
        return f"{name}:{action['experiment_id']}"
```

In `_action_summary` in `proposer/research_agent.py`, add:

```python
    if name in {"inspect_experiment", "inspect_originating_research_state"}:
        return f"action={name} experiment_id={action['experiment_id']}"
```

- [ ] **Step 7: Run the focused tests**

Run:

```bash
python -m pytest tests/proposer/test_research_state_tools.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit the inspect-first protocol**

```bash
git add proposer/research_agent.py proposer/research_tools.py proposer/scientist.py tests/proposer/test_research_state_tools.py
git commit -m "feat: gate sibling memos behind experiment inspection"
```

---

### Task 4: Cold-start Coverage and Scientist Semantics

**Files:**
- Modify: `proposer/scientist.py`
- Modify: `proposer/prompts/proposer.md`
- Create: `tests/proposer/test_knowledge_exchange_context.py`

**Interfaces:**
- Consumes: `memory_service.build_coverage_pack(current_round=int) -> str`.
- Produces: `_build_research_start_messages(first_round: bool, seed_pack: str | None, coverage_pack: str | None) -> list[dict]` used by `ScientistAgent.research`.

- [ ] **Step 1: Write failing pure context tests**

Create `tests/proposer/test_knowledge_exchange_context.py`:

```python
"""Knowledge Exchange context stays factual and reaches cold Scientists."""
from proposer.scientist import _COLD_START, _build_research_start_messages


def test_cold_start_receives_seed_then_global_coverage():
    messages = _build_research_start_messages(
        first_round=True,
        seed_pack="CHILD WORLD SEED",
        coverage_pack="GLOBAL COVERAGE",
    )
    assert [item["content"] for item in messages] == [
        _COLD_START,
        "CHILD WORLD SEED",
        "GLOBAL COVERAGE",
    ]


def test_resume_starts_with_recomputed_coverage_only():
    messages = _build_research_start_messages(
        first_round=False,
        seed_pack="OLD SEED MUST NOT REPLAY",
        coverage_pack="GLOBAL COVERAGE",
    )
    assert messages == [{"role": "user", "content": "GLOBAL COVERAGE"}]


def test_empty_coverage_adds_no_message():
    messages = _build_research_start_messages(
        first_round=True,
        seed_pack=None,
        coverage_pack="",
    )
    assert len(messages) == 1
```


- [ ] **Step 2: Run the new test and verify the helper is missing**

Run:

```bash
python -m pytest tests/proposer/test_knowledge_exchange_context.py -q
```

Expected: collection FAIL with `ImportError: cannot import name '_build_research_start_messages'`.

- [ ] **Step 3: Add the pure start-context builder**

Add near `_COLD_START` in `proposer/scientist.py`:

```python
def _build_research_start_messages(
    *,
    first_round: bool,
    seed_pack: str | None,
    coverage_pack: str | None,
) -> list[dict]:
    messages = (
        [{"role": "user", "content": _COLD_START}]
        if first_round else []
    )
    if first_round and seed_pack:
        messages.append({"role": "user", "content": seed_pack})
    if coverage_pack:
        messages.append({"role": "user", "content": coverage_pack})
    return messages
```

- [ ] **Step 4: Reuse one recomputed coverage pack on both context paths**

In `ScientistAgent.research`, compute coverage immediately before the
cold-start/resume branch:

```python
        coverage_pack = None
        if memory_service is not None:
            try:
                coverage_pack = memory_service.build_coverage_pack(
                    current_round=current_round,
                )
            except Exception as exc:
                print(
                    f"[scientist] coverage pack build failed: {exc}",
                    flush=True,
                )
```

Then initialize messages once:

```python
        first_round = session.is_first_round()
        messages = _build_research_start_messages(
            first_round=first_round,
            seed_pack=seed_pack,
            coverage_pack=coverage_pack,
        )
```

Use `if first_round:` for the existing cold-start bookkeeping and `else:` for
the existing world-event/reflection resume logic. Remove the old resume-only
`build_coverage_pack` block. Continue to archive `seed_pack` and world events as
before; coverage remains ephemeral and is not appended to the session archive.

- [ ] **Step 5: Add the epistemic boundary to the Scientist charter**

Add this section to `proposer/prompts/proposer.md` after “How to use experiment records”:

```markdown
## Research knowledge exchange

Experiment search is an index of ground covered across all branches. A search
hit is not evidence you have examined. Inspect a concrete Experiment before
citing `experiment:<id>` in a ResearchState.

After inspecting an Experiment, you may deliberately inspect its originating
ResearchState. That result is a `SUBJECTIVE_RESEARCH_MEMO`: an attributed
interpretation formed on its displayed source world, not a fact, instruction,
or ResearchState you inherit. Re-ground it against the current world and form
your own ResearchState.

Several inspected Experiments may inform one ResearchState. If their
interventions appear complementary, submit an ordinary Proposal to test the
combination in the current world; never assume branch gains compose.
```

- [ ] **Step 6: Run context, prompt, and tool tests**

Run:

```bash
python -m pytest tests/proposer/test_knowledge_exchange_context.py tests/proposer/test_research_state_tools.py tests/proposer/test_l2_memory.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Run the full regression suite**

Run:

```bash
python -m pytest -q
```

Expected: all tests pass. No test may show a changed Frontier score or budget.

- [ ] **Step 8: Check formatting and scope**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. Only the five implementation files, three test
files, and the user's pre-existing untracked chat document appear; the chat
document remains untracked.

- [ ] **Step 9: Commit the context and charter**

```bash
git add proposer/scientist.py proposer/prompts/proposer.md tests/proposer/test_knowledge_exchange_context.py
git commit -m "feat: share coverage with every scientist episode"
```

---

### Task 5: Neutral Contrasting/Diverse Discovery and Source-world Indexing

**Files:**
- Modify: `proposer/l2_memory.py`
- Modify: `tests/proposer/test_l2_memory.py`

**Interfaces:**
- Consumes: Task 1's evidence rows and aggregate coverage.
- Produces: bucketed search with non-empty `contrasting`/`diverse` views and an explicit parent `source_world` on every search hit; neutral example Experiment ids in coverage.

- [ ] **Step 1: Write failing neutral-discovery tests**

Append to `tests/proposer/test_l2_memory.py`:

```python
def test_bucketed_search_surfaces_contrasting_and_path_diverse_evidence(
    store: ResearchStore,
):
    ids = _seed_sibling_experiments(store)
    result = L2MemoryService(store.path.parent).search_experiments(
        "lookup", limit=10, buckets=True,
    )

    all_rows = [
        *result["relevant"],
        *result["contrasting"],
        *result["diverse"],
    ]
    assert {row["experiment_id"] for row in result["relevant"]} == {
        "exp-layout", "exp-cache",
    }
    assert result["contrasting"]
    assert {row["experiment_id"] for row in result["diverse"]} == {
        "exp-layout", "exp-cache",
    }
    assert all(row["source_world"] == {
        "node_id": ids["root_node_id"], "sha": "sha-root",
    } for row in all_rows)
    assert all("instruction" not in row for row in all_rows)


def test_coverage_pack_exposes_neutral_evidence_locator(
    store: ResearchStore,
):
    ids = _seed_sibling_experiments(store)
    text = L2MemoryService(store.path.parent).build_coverage_pack()
    assert f"examples=exp-layout@{ids['root_node_id']}" in text
    assert f"examples=exp-cache@{ids['root_node_id']}" in text
```

- [ ] **Step 2: Run tests and verify the current empty buckets fail**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: FAIL because `contrasting` and `diverse` are empty, search rows have an ambiguous `sha` field instead of `source_world`, and coverage has no evidence locator.

- [ ] **Step 3: Add neutral example locators to coverage**

Extend each aggregate row in `build_coverage_pack`:

```python
                row = by_path.setdefault(
                    path,
                    {
                        "experiments": 0,
                        "gate_passed": 0,
                        "gate_failed": 0,
                        "examples": [],
                    },
                )
                row["experiments"] += 1
                key = "gate_passed" if experiment.gate_result.passed else "gate_failed"
                row[key] += 1
                row["examples"].append(
                    f"{experiment.experiment_id}@{experiment.parent_node_id}"
                )
```

Render at most three examples, sorted by identity rather than performance:

```python
            examples = ",".join(sorted(row["examples"])[:3])
            lines.append(
                f"- {path}: experiments={row['experiments']} "
                f"gate_passed={row['gate_passed']} "
                f"gate_failed={row['gate_failed']} examples={examples}"
            )
```

- [ ] **Step 4: Replace ambiguous search identity and empty buckets**

In `search_experiments`, build each row from the parent world:

```python
            parent = self.queries.get_node(exp.parent_node_id)
            rows.append({
                "experiment_id": exp.experiment_id,
                "source_world": {
                    "node_id": exp.parent_node_id,
                    "sha": parent.sha if parent else None,
                },
                "child_node_id": exp.child_node_id,
                "status": exp.status,
                "gate_passed": exp.gate_result.passed,
                "metrics": dict(exp.metrics),
                "changed_paths": list(exp.changed_paths),
            })
```

Replace the final slice/empty-bucket block with deterministic, non-performance
selection:

```python
        rows.sort(key=lambda row: row["experiment_id"])
        relevant = rows[:limit]
        if not buckets:
            return {"results": relevant}
        anchor_gate = relevant[0]["gate_passed"] if relevant else None
        contrasting = [
            row for row in rows
            if anchor_gate is not None and row["gate_passed"] != anchor_gate
        ][:limit]
        diverse = []
        seen_paths: set[tuple[str, ...]] = set()
        for row in rows:
            signature = tuple(row["changed_paths"])
            if signature in seen_paths:
                continue
            seen_paths.add(signature)
            diverse.append(row)
            if len(diverse) >= limit:
                break
        return {
            "relevant": relevant,
            "contrasting": contrasting,
            "diverse": diverse,
        }
```

This ordering uses identity, gate contrast, and path coverage only. Do not sort
by metrics, objective improvement, popularity, or citation count.

- [ ] **Step 5: Run focused tests**

Run:

```bash
python -m pytest tests/proposer/test_l2_memory.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the neutral discovery view**

```bash
git add proposer/l2_memory.py tests/proposer/test_l2_memory.py
git commit -m "feat: surface neutral cross-branch evidence views"
```

---

## Final Verification

- [ ] Run focused Knowledge Exchange tests:

```bash
python -m pytest tests/proposer/test_l2_memory.py tests/proposer/test_research_state_tools.py tests/proposer/test_knowledge_exchange_context.py -q
```

Expected: all pass.

- [ ] Run the complete suite:

```bash
python -m pytest -q
```

Expected: all pass.

- [ ] Verify no forbidden persistence or workflow was introduced:

```bash
rg -n "Knowledge(Object|Store)|SynthesisProposal|merge_branch" proposer simpleevo tests
```

Expected: no new implementation symbols matching those names.

- [ ] Verify the final diff and user-file isolation:

```bash
git diff --check
git status --short
```

Expected: clean tracked worktree after the task commits; only
`docs/chat/2026.8.22.2.57.gpt聊知识共享.md` remains untracked.
