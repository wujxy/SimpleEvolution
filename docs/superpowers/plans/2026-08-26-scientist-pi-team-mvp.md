# Scientist PI and Fresh Research Team MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单 Scientist 重构为持续负责课题判断的 PI，并让 Searcher、Proposer、Executor、Challenger 以固定团队席位、fresh 异步 engagement 和角色化上下文独立承担研究劳动，为后续单独开展的 LLM interview 提供可测试实现。

**Architecture:** Scientist 的 system context 只承载稳定身份、Research Charter、Research Team Constitution、研究目标与运行边界；阶段性的 Current Research Judgment 作为普通、可修正的 L1 working-memory message 存在，历史 revision 继续写入现有 JSONL ledger 并仅按需读取。Research Team 在模型语义中是四个长期角色席位，每次 collaboration 创建一个 fresh Claude instance；实例在一次 engagement 内完成连续的自主 trajectory，提交可归因报告后归档。工程上只保留一个 `InWorldAssistant` runtime 和四份 role/context contract，不建立四套 agent state。

**Tech Stack:** Python 3.9+、stdlib JSON/文件存储、Claude CLI、provider-native function calling、pytest。

## Global Constraints

- 本计划只改 `scientist/` 顶层单 Scientist 路径、其直接测试与行为 probe；不设计或修改 SimpleEvolution 的多分支 evolution 分工。
- 本计划取代 `docs/design/scientist完整上下文重构实施文档.md` 作为本轮 MVP 的执行依据；旧文档保留为设计演变记录，不在实现时混用。
- 不修改 `.env`、密钥、生产配置、隐藏 reference 或 benchmark 产物。
- 最终 assembled context 是验收单位。Charter、Team Constitution、tool descriptions、cold start、nudges、skills、compaction、reports、terminal contract 必须表达同一对象模型。
- Scientist 是唯一跨 engagement 持续存在的 research owner，负责问题选择、证据权衡、方向更新、优先级与结束判断。
- Searcher、Proposer、Executor、Challenger 是 Research Team 中长期存在的一等角色席位，不是 Scientist 的 hands、amplifier、assistant 或能力按钮。
- 每次角色调用创建 fresh collaborator instance；同一 engagement 内的 Claude CLI trajectory 可以连续搜索、阅读、编辑、运行和 debug，报告提交后实例关闭并归档。
- 四种角色调用都立即返回 receipt，并通过现有 `_spawn/_jobs/poll` 异步完成；Scientist 需要阻塞时显式 `wait`，不保留同步 collaborator 路径。
- fresh instance 不继承上一位 collaborator 的 raw transcript、推理过程或长期对话；Scientist 负责跨 engagement 整合。
- 外部呈现四个角色端点，内部共享一个 runtime。不得暴露 `ask_searcher`、`ask_proposer`、`assign_executor`、`ask_challenger` 或 `collaborate(role=...)`。
- `proposer(scope="open")` 默认看不到 Current Research Judgment、Scientist 的偏好、历史推理链或 Scientist 筛选的 experiment ids；runtime 自动提供全部中性 thin evidence index。`proposer(scope="directed")` 才获得 Scientist 明确指定的 region 与客观 evidence。
- Searcher 默认看研究问题而不看 Scientist 的预期答案；Executor 看完整研究意图、约束、成功标准和当前 world；Challenger 看 Current Research Judgment 及其证据，才能真正攻击它。
- Current Research Judgment 是可空、可冲突、可推翻的阶段性判断，不是 canonical truth，不进入 system prompt，不要求 cold start 先写入，也不因 collaborator 完工机械更新。
- L2 历史复用 `.scientist/research_state.jsonl` 的 append-only revisions；只增加薄索引和按 id 读取，不在 MVP 引入 SQLite、迁移或跨分支查询。
- notebook、notes、session trajectory、raw collaborator transcript 和历史 judgment 默认不注入 active context。
- Scientist 可以直接读代码、查关键事实和做小型判别 probe；生产性实现、长 debug、训练和 measurement campaign 默认交给 Executor。
- collaborator report 是有来源的 testimony，不是 Scientist belief；runtime 不自动解释报告或修改 Current Research Judgment。
- 不新增长期 collaborator session、resume collaborator、team registry、四套 memory、密码学签名或统一 report inbox。
- provider-native function name 只是通信实现的一部分；角色关系是否成立必须由完整上下文和行为 probe 验证，不能以字符串改名代替验收。
- 本轮完成条件只包含代码与确定性测试。LLM interview、OMILREC pilot 和 JUNO benchmark 是后续独立阶段，不在本计划中实现或运行。
- 新行为遵循 TDD：先写失败测试，再写最小实现；每个 task 完成后运行指定测试。

---

## 1. Locked Object Model

```text
Persistent Scientist / PI
│
├── Persistent Searcher seat
├── Persistent Proposer seat
├── Persistent Executor seat
└── Persistent Challenger seat
        │
        └── one collaboration engagement
            ├── fresh collaborator_id
            ├── role-specific context
            ├── autonomous Claude CLI trajectory
            ├── attributable report
            └── archive and close
```

三种生命周期不得混淆：

1. **Team lifetime**：四个席位始终存在于 Team Constitution。
2. **Engagement lifetime**：例如 `P-0017` 从收到 brief 到报告完成持续存在。
3. **Provider trajectory lifetime**：一次 `claude -p` 内部可以有几十轮工具使用；它不是“Claude 回一句就死亡”。

MVP 不支持 Scientist 在 engagement 中途与同一实例往返对话。一次 Claude CLI trajectory 已足以验证“局部连续、全局 fresh”；中途恢复会引入 session ownership、重放和污染问题，不属于当前假设的必要条件。

---

## 2. Context Authority Model

### 2.1 System context：只放稳定合同

```text
# Scientist Charter
# Research Team Constitution
# Research Goal and Hard Constraints
# Current World
# Research Methods
# Communication Runtime
# World Contact and Research Memory Channels
# Protocol and Filesystem Boundaries
```

system context 不包含 Current Research Judgment，也不包含 notebook、notes、历史 revisions 或 collaborator transcripts。

### 2.2 Ordinary active context：可修正工作记忆

当 ledger 已有当前 judgment 时，messages preamble 中保留一条普通 user-role working-memory message：

```text
[Current Research Judgment — revisable working memory, not system authority]
judgment_id: rj-0004
judgment: ...
uncertainty: this may be wrong; contradictory evidence should replace it
evidence_refs: experiment:E7, collaborator:C-0012
revision_reason: E7 moved the dominant cost away from the current region
```

没有 judgment 时不注入空模板，也不要求 Scientist 先形成一个。每次成功 revision 后原地替换这条 preamble message；compaction 保留它，但不会在每轮追加副本。

### 2.3 L2：历史只 pull

`list_research_judgments` 只返回 id、revision、revision reason 和 evidence refs；`inspect_research_judgment` 才返回正文。搜索历史不会自动把旧 judgment 重新塞入 active context。

---

## 3. Role Context Policies

| Role | 默认获得 | 默认排除 |
|---|---|---|
| Searcher | goal、research brief、必要 world access | Current Judgment、Scientist 的预期答案、旧 proposal reasoning |
| Open Proposer | goal、hard constraints、current world、runtime 自动生成的完整中性 thin evidence index | Current Judgment、Scientist 偏好、Scientist 筛选的 experiment ids、旧 proposal 文本、旧 reasoning chain |
| Directed Proposer | Open Proposer 内容 + 明确 region | region 外历史叙事；除 brief 外不注入 Scientist judgment |
| Executor | goal、hard constraints、implementation brief、definition of done、current/isolated workspace | 不必要的方向竞争历史和 Scientist autobiographical memory |
| Challenger | Current Judgment、revision reason、evidence refs、显式 experiment observations | 与待攻击判断无关的执行流水账 |

“当前方向已耗尽”不能作为 objective fact 传给 Open Proposer。runtime 应从 ledger 的所有实验机械生成只含 id、status、gate、metrics、changed paths 的中性索引，由 Proposer 自己判断哪些 evidence 值得继续读取。

---

## 4. File Structure and Responsibility

### Create

- `scientist/prompts/scientist.md`：稳定 PI Charter。
- `scientist/prompts/research_team.md`：四个席位、ownership、freshness 与报告地位。
- `scientist/collaboration.py`：四个 role contracts、context policy renderer 和 report envelope；不运行 subprocess。
- `tests/scientist/test_context_contract.py`：完整 assembled context、authority layer 与矛盾语义测试。
- `tests/scientist/test_collaboration.py`：四角色 schema、context filtering、fresh/local lifecycle 与 report attribution 测试。

### Modify

- `scientist/agent.py`：静态 system assembler、judgment preamble、四角色 dispatch、reports、cold start、nudges、compaction 和 terminal semantics。
- `scientist/native_tools.py`：用四个角色端点替换 `consult/work`，重写 communication/runtime 文案，增加 judgment history pull channels。
- `scientist/assistant_tools.py`：继续作为唯一 collaborator runtime；接受 role contract，生成 fresh id，执行一次完整 Claude trajectory，归档并返回可归因报告。
- `scientist/ledger.py`：轻量 Current Research Judgment revision、current head、薄历史索引和按 id 读取；复用 JSONL。
- `scientist/cli.py`：启动/resume 时把 current judgment 放入 ordinary messages，不再把 notebook/notes 放入 system。
- `scientist/scientist_session.py`：明确 session/notebook 是 archive，不是 active cognition。
- `scientist/research_skills/claude_use.md`：从“如何用 Claude 工具”改为“如何与研究团队协作”。
- `scientist/research_skills/reframe_inherited_problem.md`：以 live world 与 evidence 重建判断，不把旧 judgment 当事实。
- `tests/scientist/test_oneworld.py`：更新 runtime、wire invariant、compaction、optional judgment 和 report tests。
- `tests/scientist/test_model_native_tools.py`：更新 provider-native schema 和 action routing tests。

### Preserve as archives

- `.scientist/session/session.jsonl`
- `.scientist/session/notebook.md`
- `.scientist/notes.md`
- `.scientist/assistant/{collaborator_id}/raw.txt`
- `.scientist/assistant/{collaborator_id}/digest.json`

这些文件可以由人或显式读取渠道审计，但不进入默认 active context。

---

### Task 1: Establish the Complete PI/Team Context Contract

**Files:**
- Create: `scientist/prompts/scientist.md`
- Create: `scientist/prompts/research_team.md`
- Create: `tests/scientist/test_context_contract.py`
- Modify: `scientist/agent.py`

**Interfaces:**
- Produces: `build_system_prompt(spec: dict, *, roots: dict | None = None) -> str`
- Guarantees: system prompt 只由稳定合同组成；无 Current Research Judgment 参数。

- [ ] **Step 1: Write the failing authority-layer tests**

```python
from scientist.agent import build_system_prompt


def _system():
    return build_system_prompt(
        {
            "goal": "understand and improve reconstruction",
            "editable_paths": ["src"],
            "gate_block": "correctness gates must pass",
        },
        roots={"work": "/work", "repo": "/repo", "scratch": "/scratch"},
    )


def test_system_context_has_stable_authority_order():
    text = _system()
    headings = [
        "# Scientist Charter",
        "# Research Team Constitution",
        "# Research Goal and Hard Constraints",
        "# Current World",
        "# Communication Runtime",
        "# World Contact and Research Memory Channels",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_fallible_judgment_is_not_system_authority():
    text = _system().lower()
    assert "# current research judgment" not in text
    assert "current judgment:" not in text
    assert "canonical cognition" not in text
```

- [ ] **Step 2: Write the failing team-object tests**

```python
def test_team_exists_before_callable_channels():
    text = _system()
    team = text.index("# Research Team Constitution")
    runtime = text.index("# Communication Runtime")
    for role in ("Searcher", "Proposer", "Executor", "Challenger"):
        assert role in text[team:runtime]


def test_system_rejects_legacy_tool_identity():
    text = _system().lower()
    forbidden = (
        "your assistant", "your hands", "your amplifier",
        "strongest coding agent", "default limb", "seat of node",
        "your lens is your identity", "ask_searcher", "assign_executor",
    )
    assert [phrase for phrase in forbidden if phrase in text] == []
```

- [ ] **Step 3: Run the tests and verify failure**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: FAIL because the packaged Scientist/Team prompts do not exist and the current assembler still injects seat/assistant/tool semantics.

- [ ] **Step 4: Add the stable Scientist Charter**

`scientist/prompts/scientist.md`:

```markdown
# Scientist Charter

You are the persistent Scientist and principal investigator responsible for
this research topic. Your scarce responsibility is scientific judgment:
deciding what the problem currently appears to be, what evidence matters,
which inquiry deserves resources next, when a direction should deepen or
change, and when the investigation can honestly conclude.

You may inspect the world and run small discriminating probes. Do not let
production implementation, long debugging, training, or measurement
campaigns occupy the context needed for global scientific judgment; those
are research engagements for your team.

Reports from collaborators and measurements from the world are evidence to
judge, not conclusions you must adopt. No collaborator may revise your
Current Research Judgment for you.
```

- [ ] **Step 5: Add the stable Research Team Constitution**

`scientist/prompts/research_team.md`:

```markdown
# Research Team Constitution

Your research team has four persistent collaborator seats:

- Searcher independently investigates sources, code, and factual questions.
- Proposer independently develops research directions; open scope may reject
  your current framing, while directed scope works inside an explicit region.
- Executor independently plans and completes implementation, debugging,
  measurement, and experiment work from a research brief and definition of
  done.
- Challenger attacks your current judgment, evidence, assumptions, and
  stopping logic.

Each engagement is occupied by a fresh Claude collaborator with its own
collaborator_id. That collaborator works through one autonomous trajectory,
reports, and is archived; the next engagement is fresh by default. The role
persists, not the individual instance or its private trajectory.

These are research colleagues, not tools, hands, extensions, or oracles.
The callable runtime attached to the conversation is only how you open an
engagement with one of them.
```

- [ ] **Step 6: Replace `build_system_prompt` with an explicit block assembler**

Keep the current `build_generation_context`, skill catalog, native protocol and boundary renderers, but assemble them under explicit headings and delete the `notebook`/`notes` parameters:

```python
def build_system_prompt(spec: dict, *, roots: dict | None = None) -> str:
    from .memory.context import build_generation_context
    from .native_tools import (
        NATIVE_PROTOCOL_BLOCK,
        NATIVE_RUNTIME_BLOCK,
        render_native_boundaries,
    )
    from .prompts import load_semantic

    roots = roots or {}
    charter = str(spec.get("charter") or "").strip()
    if not charter:
        charter = load_semantic("scientist").strip()
    world = build_generation_context(
        goal=spec.get("goal") or "(no goal stated)",
        editable=list(spec.get("editable_paths") or []),
        frozen=[],
        base_sha=spec.get("base_sha") or "—" * 40,
        gate_block=spec.get("gate_block") or "(no gates stated)",
    )
    methods = (
        "# Research Methods\n\n"
        + render_startup_skills()
        + "\n\nOptional methods:\n"
        + render_research_skill_catalog()
    )
    return "\n\n".join([
        charter,
        load_semantic("research_team").strip(),
        "# Research Goal and Hard Constraints\n\n" + world,
        "# Current World\n\nThe live workspace and new evidence outrank memory.",
        methods,
        "# Communication Runtime\n\n" + NATIVE_RUNTIME_BLOCK,
        "# World Contact and Research Memory Channels\n\n"
        "The provider schemas describe available channels; they do not "
        "define the identity of your colleagues.",
        NATIVE_PROTOCOL_BLOCK,
        render_native_boundaries(
            str(roots.get("work") or "/work"),
            str(roots.get("repo") or "/repo"),
            str(roots.get("scratch") or "/scratch"),
        ),
    ])
```

`spec["charter"]` remains a supported override and replaces only `scientist.md`; `research_team.md` is always appended. There is no fallback to `proposer.md`.

- [ ] **Step 7: Run the context tests**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the context contract**

```bash
git add scientist/prompts/scientist.md scientist/prompts/research_team.md scientist/agent.py tests/scientist/test_context_contract.py
git commit -m "refactor: establish scientist PI team context"
```

---

### Task 2: Expose Four Role Seats over One Collaborator Runtime

**Files:**
- Create: `scientist/collaboration.py`
- Create: `tests/scientist/test_collaboration.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/assistant_tools.py`
- Modify: `scientist/agent.py`
- Modify: `tests/scientist/test_model_native_tools.py`
- Modify: `tests/scientist/test_oneworld.py`

**Interfaces:**
- Produces: `ROLE_NAMES = frozenset({"searcher", "proposer", "executor", "challenger"})`
- Produces: `build_collaboration_prompt(role: str, action: dict, *, goal: str, gate_block: str, current_judgment: dict | None, evidence_index: list[dict], selected_experiments: list[dict]) -> str`
- Produces: `InWorldAssistant.engage(role: str, action: dict) -> dict`
- Produces: `LocalLedger.neutral_experiment_index() -> list[dict]`
- Provider surface: four separate functions named `searcher`, `proposer`, `executor`, `challenger`; no generic role argument.

- [ ] **Step 1: Write failing provider-surface tests**

```python
from scientist.native_tools import NATIVE_TOOLS


def _schemas():
    return {tool["function"]["name"]: tool["function"] for tool in NATIVE_TOOLS}


def test_team_roles_are_first_class_endpoints():
    schemas = _schemas()
    assert {"searcher", "proposer", "executor", "challenger"} <= set(schemas)
    assert "consult" not in schemas
    assert "work" not in schemas
    assert "ask_collaborator" not in schemas
    for role in ("searcher", "proposer", "executor", "challenger"):
        assert "role" not in schemas[role]["parameters"]["properties"]


def test_proposer_has_open_and_directed_scope():
    scope = _schemas()["proposer"]["parameters"]["properties"]["scope"]
    assert scope["enum"] == ["open", "directed"]
```

- [ ] **Step 2: Write failing context-policy tests**

```python
from scientist.collaboration import build_collaboration_prompt


JUDGMENT = {
    "current_judgment": "The cache path is still the only worthwhile direction.",
    "revision_reason": "three local wins",
    "evidence_refs": ["experiment:E3"],
}
EVIDENCE_INDEX = [{
    "experiment_id": "E3",
    "status": "COMPLETED", "gate_passed": True,
    "metrics": {"runtime_ms": 810.0},
    "changed_paths": ["src/cache.cc"],
}, {
    "experiment_id": "E4",
    "status": "COMPLETED", "gate_passed": True,
    "metrics": {"runtime_ms": 790.0},
    "changed_paths": ["src/memory.cc"],
}]


def _prompt(role, action):
    return build_collaboration_prompt(
        role,
        action,
        goal="minimize runtime with correctness gates green",
        gate_block="correctness gates must pass",
        current_judgment=JUDGMENT,
        evidence_index=EVIDENCE_INDEX,
        selected_experiments=[],
    )


def test_open_proposer_does_not_inherit_scientist_judgment():
    text = _prompt("proposer", {"brief": "find the next direction", "scope": "open"})
    assert "cache path is still the only worthwhile direction" not in text
    assert "three local wins" not in text
    assert "E3" in text and "E4" in text
    assert "memory.cc" in text


def test_directed_proposer_receives_region_not_autobiography():
    text = _prompt("proposer", {
        "brief": "find a structural optimization",
        "scope": "directed",
        "region": "cache evaluation",
    })
    assert "cache evaluation" in text
    assert "three local wins" not in text


def test_searcher_does_not_receive_expected_answer():
    text = _prompt("searcher", {"brief": "locate the dominant allocation path"})
    assert "cache path is still the only worthwhile direction" not in text


def test_challenger_receives_the_judgment_it_must_attack():
    text = _prompt("challenger", {"brief": "find the strongest failure mode"})
    assert "cache path is still the only worthwhile direction" in text
    assert "three local wins" in text


def test_executor_receives_intent_constraints_and_definition_of_done():
    text = _prompt("executor", {
        "brief": "implement a TOF-aware cache",
        "definition_of_done": "correctness passes; report runtime_ms",
        "workspace": "current",
    })
    assert "TOF-aware cache" in text
    assert "correctness passes" in text
    assert "minimize runtime" in text
```

- [ ] **Step 3: Run the new tests and verify failure**

Run: `python -m pytest tests/scientist/test_collaboration.py tests/scientist/test_model_native_tools.py -q`

Expected: FAIL because the four schemas and `scientist.collaboration` do not exist.

- [ ] **Step 4: Add the role contracts and prompt renderer**

`scientist/collaboration.py` owns semantic policy only:

```python
from __future__ import annotations

import json

ROLE_NAMES = frozenset({"searcher", "proposer", "executor", "challenger"})


def _objective_experiments(experiments: list[dict]) -> str:
    rows = []
    for item in experiments:
        rows.append({
            "experiment_id": item.get("experiment_id"),
            "intervention": item.get("intervention"),
            "observation": item.get("observation"),
        })
    return json.dumps(rows, ensure_ascii=False, indent=2)


def build_collaboration_prompt(
    role: str,
    action: dict,
    *,
    goal: str,
    gate_block: str,
    current_judgment: dict | None,
    evidence_index: list[dict],
    selected_experiments: list[dict],
) -> str:
    if role not in ROLE_NAMES:
        raise ValueError(f"unknown collaborator role: {role}")
    brief = str(action.get("brief") or "").strip()
    if not brief:
        raise ValueError(f"{role}.brief must be non-empty")
    sections = [
        f"You are a fresh {role.title()} collaborator in a research team.",
        "Own this engagement: plan and execute the investigation yourself, "
        "challenge the brief when evidence requires it, and return your own "
        "attributable research report.",
        f"Research goal:\n{goal}",
        f"Hard constraints:\n{gate_block}",
        f"Engagement brief:\n{brief}",
    ]
    if selected_experiments:
        sections.append(
            "Selected objective experiment observations:\n"
            + _objective_experiments(selected_experiments)
        )
    if role == "proposer":
        scope = str(action.get("scope") or "")
        if scope not in {"open", "directed"}:
            raise ValueError("proposer.scope must be open|directed")
        sections.append(f"Proposal scope: {scope}")
        if scope == "directed":
            region = str(action.get("region") or "").strip()
            if not region:
                raise ValueError("directed proposer requires region")
            sections.append(f"Directed research region:\n{region}")
        else:
            sections.append(
                "Reconstruct opportunities from the goal, live world, and "
                "neutral evidence index below. You have intentionally not "
                "received the Scientist's current preference, selected "
                "experiment ids, or reasoning history.\n\nNeutral evidence "
                "index:\n" + json.dumps(
                    evidence_index, ensure_ascii=False, indent=2)
            )
    if role == "challenger":
        sections.append(
            "Judgment to attack:\n"
            + json.dumps(current_judgment or {
                "status": "no stable current judgment"
            }, ensure_ascii=False, indent=2)
        )
    if role == "executor":
        done = str(action.get("definition_of_done") or "").strip()
        if not done:
            raise ValueError("executor.definition_of_done must be non-empty")
        sections.append(f"Definition of done:\n{done}")
    sections.append(
        "Your private trajectory is not the Scientist's memory. Return only "
        "a concise report of conclusions, evidence, artifacts, uncertainty, "
        "and recommended follow-up."
    )
    return "\n\n".join(sections)
```

- [ ] **Step 5: Replace `CONSULT_TOOL` and `WORK_TOOL` with four role schemas**

Use `brief` as the common assignment contract. `proposer` also requires `scope`; `executor` also requires `definition_of_done`. Keep runtime-specific fields minimal:

```python
SEARCHER_TOOL = _fn("searcher", "Open an engagement with a fresh Searcher colleague.", {
    "brief": {"type": "string"},
    "read": {"type": "string", "enum": ["none", "node", "lab"]},
    "experiment_ids": {"type": "array", "items": {"type": "string"}},
}, ["brief"])

PROPOSER_TOOL = _fn("proposer", "Open an engagement with a fresh Proposer colleague.", {
    "brief": {"type": "string"},
    "scope": {"type": "string", "enum": ["open", "directed"]},
    "region": {"type": "string"},
    "experiment_ids": {"type": "array", "items": {"type": "string"}},
}, ["brief", "scope"])

EXECUTOR_TOOL = _fn("executor", "Open an engagement with a fresh Executor colleague.", {
    "brief": {"type": "string"},
    "definition_of_done": {"type": "string"},
    "workspace": {"type": "string", "enum": ["current", "isolated"]},
    "timeout_minutes": {"type": "integer", "minimum": 1, "maximum": 180},
}, ["brief", "definition_of_done"])

CHALLENGER_TOOL = _fn("challenger", "Open an engagement with a fresh Challenger colleague.", {
    "brief": {"type": "string"},
    "experiment_ids": {"type": "array", "items": {"type": "string"}},
}, ["brief"])
```

Descriptions may explain how to open an engagement, but must not describe the role itself as a tool or action.

- [ ] **Step 6: Add one runtime entry point with fresh IDs**

First add a mechanical thin index to `LocalLedger`; it returns every experiment and never includes instruction, proposal text or originating research state:

```python
def neutral_experiment_index(self) -> list[dict]:
    rows = [self._search_row(experiment) for experiment in self._experiments()]
    rows.sort(key=lambda row: str(row.get("experiment_id") or ""))
    return rows
```

Extend `AssistantConfig.from_spec` to retain `goal` and `gate_block`. Add this public method to the existing `InWorldAssistant`; keep `_spawn`, `_jobs`, `poll`, `shutdown` and raw archive mechanics shared:

```python
def engage(self, role: str, action: dict) -> dict:
    collaborator_id = self._next_call_id(role)
    evidence_index = self.ledger.neutral_experiment_index()
    selected_experiments = []
    if not (role == "proposer" and action.get("scope") == "open"):
        for experiment_id in action.get("experiment_ids") or []:
            result = self.ledger.inspect_experiment({"experiment_id": experiment_id})
            if result.get("ok"):
                selected_experiments.append(result)
    prompt = build_collaboration_prompt(
        role,
        action,
        goal=self.config.goal,
        gate_block=self.config.gate_block,
        current_judgment=self.ledger.current_judgment(),
        evidence_index=evidence_index,
        selected_experiments=selected_experiments,
    )
    return self._start_engagement(collaborator_id, role, action, prompt)
```

Every role returns the same receipt shape immediately:

```python
return {
    "ok": True,
    "status": "running",
    "role": role,
    "collaborator_id": collaborator_id,
    "outstanding_jobs": [job.collaborator_id for job in self._jobs],
}
```

`_start_engagement` reuses `_spawn` and records role/report contract on the existing `_Job`. Executor maps `workspace=current|isolated` onto the existing main/side-world behavior; other roles use read-only tools. Completed reports keep the existing single `poll` intake point in `agent.py` so they cannot split an assistant tool-call from its tool result.

Apply these access rules without creating separate runtimes:

- Searcher uses `read=none|node|lab` and the existing read-only Claude tool set;
- Proposer always starts in the live lab with the read-only Claude tool set, so it can reconstruct opportunities from the actual world;
- Challenger starts in the live lab with the read-only Claude tool set;
- Executor alone receives edit/write/bash tools and uses the selected current or isolated workspace.

The cognitive roles can therefore run long search/read trajectories, but cannot modify the world.

- [ ] **Step 7: Route four names without exposing a generic dispatcher**

In `dispatch_action`:

```python
if name in ROLE_NAMES:
    return assistant.engage(name, action)
```

`ROLE_NAMES` is an internal implementation constant; no provider schema accepts a `role` argument.

- [ ] **Step 8: Test engagement-local continuity and global freshness**

Use the existing fake Claude CLI and assert:

```python
def test_two_proposer_engagements_get_distinct_instances(runtime):
    first = runtime.engage("proposer", {"brief": "scan broadly", "scope": "open"})
    second = runtime.engage("proposer", {"brief": "scan again", "scope": "open"})
    assert first["collaborator_id"] != second["collaborator_id"]
    assert first["role"] == second["role"] == "proposer"
    assert first["status"] == second["status"] == "running"


def test_executor_is_one_long_autonomous_engagement(runtime):
    receipt = runtime.engage("executor", {
        "brief": "implement, compile, debug, and benchmark the change",
        "definition_of_done": "tests pass and metrics are reported",
        "workspace": "current",
    })
    assert receipt["status"] == "running"
    assert receipt["role"] == "executor"
    assert receipt["collaborator_id"].startswith("executor-")
```

The test must not add a follow-up or resume API.

Add a separate test proving Searcher、Proposer and Challenger all return before their fake Claude process exits, then `wait`/`poll` delivers their attributed reports. This locks in asynchronous semantics for all four roles.

- [ ] **Step 9: Run role, wire and async tests**

Run: `python -m pytest tests/scientist/test_collaboration.py tests/scientist/test_model_native_tools.py tests/scientist/test_oneworld.py -q`

Expected: PASS; provider wire adjacency tests remain green.

- [ ] **Step 10: Commit the role runtime**

```bash
git add scientist/collaboration.py scientist/native_tools.py scientist/assistant_tools.py scientist/agent.py tests/scientist/test_collaboration.py tests/scientist/test_model_native_tools.py tests/scientist/test_oneworld.py
git commit -m "feat: add fresh research team engagements"
```

---

### Task 3: Make Current Research Judgment Optional L1 and History Pull-Only L2

**Files:**
- Modify: `scientist/ledger.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/agent.py`
- Modify: `scientist/cli.py`
- Modify: `tests/scientist/test_oneworld.py`
- Modify: `tests/scientist/test_model_native_tools.py`

**Interfaces:**
- Produces: `LocalLedger.revise_research_judgment(action: dict) -> dict`
- Produces: `LocalLedger.current_judgment() -> dict | None`
- Produces: `LocalLedger.list_research_judgments(action: dict) -> dict`
- Produces: `LocalLedger.inspect_research_judgment(action: dict) -> dict`
- Produces: `_upsert_judgment_message(messages: list[dict], judgment: dict | None) -> None`
- Provider channels: `revise_research_judgment`, `list_research_judgments`, `inspect_research_judgment`

- [ ] **Step 1: Write failing optional-state and history tests**

```python
def test_current_judgment_may_be_absent(ledger):
    assert ledger.current_judgment() is None


def test_judgment_revision_is_append_only_and_may_be_uncertain(ledger):
    first = ledger.revise_research_judgment({
        "judgment": "No stable mechanism yet; allocation and cache costs remain plausible.",
        "revision_reason": "Initial probes conflict.",
        "evidence_refs": ["experiment:E1"],
    })
    second = ledger.revise_research_judgment({
        "judgment": "Allocation lifetime now appears primary; cache cost remains uncertain.",
        "revision_reason": "E2 moved cache cost below 10% while allocation dominated.",
        "evidence_refs": ["experiment:E2"],
    })
    assert first["judgment_id"] == "rj-0001"
    assert second["judgment_id"] == "rj-0002"
    assert ledger.current_judgment()["judgment_id"] == "rj-0002"


def test_history_index_is_thin_and_detail_is_pull_only(ledger):
    ledger.revise_research_judgment({
        "judgment": "private long judgment body",
        "revision_reason": "new evidence",
        "evidence_refs": ["experiment:E3"],
    })
    index = ledger.list_research_judgments({"limit": 10})
    assert "judgment" not in index["results"][0]
    detail = ledger.inspect_research_judgment({"judgment_id": "rj-0001"})
    assert detail["judgment"] == "private long judgment body"
```

- [ ] **Step 2: Write failing authority and compaction tests**

```python
from scientist.agent import _compact_native, _upsert_judgment_message


def test_judgment_is_an_ordinary_revisable_message_not_system_text():
    messages = [{"role": "user", "content": "begin"}]
    judgment = {
        "judgment_id": "rj-0001",
        "judgment": "Cache cost and allocation are both plausible.",
        "revision_reason": "evidence conflicts",
        "evidence_refs": ["experiment:E1"],
    }
    _upsert_judgment_message(messages, judgment)
    assert messages[1]["role"] == "user"
    assert "revisable working memory" in messages[1]["content"]
    assert "both plausible" in messages[1]["content"]


def test_judgment_message_is_replaced_not_repeated_and_survives_compaction():
    messages = [{"role": "user", "content": "begin"}]
    _upsert_judgment_message(messages, {
        "judgment_id": "rj-0001", "judgment": "old",
        "revision_reason": "first", "evidence_refs": [],
    })
    _upsert_judgment_message(messages, {
        "judgment_id": "rj-0002", "judgment": "new",
        "revision_reason": "revision", "evidence_refs": [],
    })
    messages.extend(_turn(i) for i in range(8))
    messages[:] = [item for group in messages for item in (group if isinstance(group, list) else [group])]
    _compact_native(messages, keep_messages=4, max_chars=1000)
    blocks = [m for m in messages if "Current Research Judgment" in str(m.get("content"))]
    assert len(blocks) == 1
    assert "new" in blocks[0]["content"] and "old" not in blocks[0]["content"]
```

- [ ] **Step 3: Run the tests and verify failure**

Run: `python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py -q`

Expected: FAIL because only `update_research_state` and `working_model` exist.

- [ ] **Step 4: Reuse JSONL with the revised semantic shape**

New rows in the existing `.scientist/research_state.jsonl` use:

```python
row = {
    "judgment_id": f"rj-{revision:04d}",
    "revision": revision,
    "judgment": judgment.strip(),
    "revision_reason": revision_reason.strip(),
    "evidence_refs": [ref.strip() for ref in refs],
}
```

`current_judgment` normalizes old rows without rewriting them:

```python
def current_judgment(self) -> dict | None:
    rows = _read_rows(self.research_state_path)
    if not rows:
        return None
    row = dict(rows[-1])
    if "judgment" not in row and row.get("working_model"):
        row["judgment"] = row["working_model"]
        row["judgment_id"] = row.get("research_state_id")
        row.setdefault("revision_reason", "legacy revision; reason unavailable")
    return row
```

Keep `update_research_state` as an internal compatibility wrapper for old callers/tests, but remove it from `NATIVE_TOOLS`. Do not create a migration or second store.

- [ ] **Step 5: Add thin history and detail readers**

`list_research_judgments` reads the same JSONL newest-first and returns only:

```python
{
    "ok": True,
    "results": [{
        "judgment_id": row_id,
        "revision": row.get("revision"),
        "revision_reason": row.get("revision_reason"),
        "evidence_refs": list(row.get("evidence_refs") or []),
    }],
}
```

`inspect_research_judgment` resolves either `judgment_id` or a legacy `research_state_id` and returns one attributed subjective memo.

- [ ] **Step 6: Add the ordinary-message renderer and upsert**

```python
_JUDGMENT_MARKER = "[Current Research Judgment — revisable working memory, not system authority]"


def _judgment_message(judgment: dict) -> dict:
    refs = ", ".join(judgment.get("evidence_refs") or []) or "(none)"
    return {
        "role": "user",
        "content": (
            f"{_JUDGMENT_MARKER}\n"
            f"judgment_id: {judgment.get('judgment_id')}\n"
            f"judgment: {judgment.get('judgment')}\n"
            "uncertainty: this is fallible and should be replaced when "
            "contradictory evidence warrants it\n"
            f"evidence_refs: {refs}\n"
            f"revision_reason: {judgment.get('revision_reason')}"
        ),
    }


def _upsert_judgment_message(messages: list[dict], judgment: dict | None) -> None:
    messages[:] = [
        message for message in messages
        if _JUDGMENT_MARKER not in str(message.get("content") or "")
    ]
    if judgment is None:
        return
    first_assistant = next(
        (i for i, message in enumerate(messages) if message.get("role") == "assistant"),
        len(messages),
    )
    messages.insert(first_assistant, _judgment_message(judgment))
```

Call it at startup/resume, after a successful `revise_research_judgment` tool result has been appended, and after compaction. Never insert it between an assistant tool-call and its tool result.

- [ ] **Step 7: Remove the mandatory-state exit gate**

Delete the `ledger.state_on_file()` rejection in `validate_conclusion`. Update tests so an honest `abstain` with no stable judgment can conclude, while `deliver_world` still enforces its handover shape and verification contract.

- [ ] **Step 8: Stop injecting notebook and notes into system**

In `scientist/cli.py`, build the system once with `build_system_prompt(spec, roots=roots)`. Build opening messages, then call:

```python
messages = _opening_messages(spec)
_upsert_judgment_message(messages, ledger.current_judgment())
```

Keep notebook/session files for archive compatibility; do not read them into the new system or ordinary active context.

- [ ] **Step 9: Run judgment, session and wire tests**

Run: `python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_scientist_session.py tests/scientist/test_model_native_tools.py -q`

Expected: PASS; old JSONL rows remain readable and no SQLite file is created.

- [ ] **Step 10: Commit lightweight research judgment**

```bash
git add scientist/ledger.py scientist/native_tools.py scientist/agent.py scientist/cli.py tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py
git commit -m "feat: add revisable research judgment memory"
```

---

### Task 4: Remove Contradictory Single-Agent Semantics End to End

**Files:**
- Modify: `scientist/agent.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/assistant_tools.py`
- Modify: `scientist/scientist_session.py`
- Modify: `scientist/research_skills/claude_use.md`
- Modify: `scientist/research_skills/reframe_inherited_problem.md`
- Modify: `tests/scientist/test_context_contract.py`
- Modify: `tests/scientist/test_collaboration.py`
- Modify: `tests/scientist/test_oneworld.py`

**Interfaces:**
- Produces: `_collaborator_report_message(result: dict) -> str`
- Guarantees: cold start、nudges、skills、reports、compaction 和 terminal contract 不会否认 PI/Team Constitution。

- [ ] **Step 1: Add a whole-surface contradiction test**

Build one string from the production system prompt, every provider schema description, cold start, budget/time nudges and loaded research skills:

```python
import json

from scientist.agent import _BUDGET_NUDGE, _COLD_START, _TIME_NUDGE, build_system_prompt
from scientist.native_tools import NATIVE_PROTOCOL_BLOCK, NATIVE_RUNTIME_BLOCK, NATIVE_TOOLS
from scientist.research_skills import load_research_skill, render_startup_skills


def complete_reachable_context():
    return "\n".join([
        build_system_prompt({"goal": "g", "editable_paths": ["src"]}),
        json.dumps(NATIVE_TOOLS, ensure_ascii=False),
        NATIVE_RUNTIME_BLOCK,
        NATIVE_PROTOCOL_BLOCK,
        _COLD_START,
        _BUDGET_NUDGE,
        _TIME_NUDGE,
        render_startup_skills(),
        load_research_skill("claude_use"),
        load_research_skill("reframe_inherited_problem"),
    ])


def test_every_reachable_surface_obeys_the_same_object_model():
    text = complete_reachable_context().lower()
    forbidden = (
        "your assistant", "your hands", "your amplifier", "default limb",
        "strongest executor", "strongest coding agent", "assistant finished",
        "after every work cycle", "form an initial model before",
        "your lens is your identity", "ask_proposer", "assign_executor",
    )
    assert [phrase for phrase in forbidden if phrase in text] == []
```

`complete_reachable_context()` must call the real assembler/loaders and serialize real `NATIVE_TOOLS`; it must not duplicate expected prompt text inside the test.

- [ ] **Step 2: Rewrite cold start and nudges as PI decisions**

Cold start must explicitly allow any of these first moves: inspect world, run a small probe, open Searcher/Proposer/Executor engagement, or remain without a stable judgment. Budget/time nudges may request synthesis and conclusion but must not require a judgment revision or tell Scientist to perform heavy work personally.

Use this cold-start contract:

```python
_COLD_START = (
    "You are beginning or resuming this investigation as its Scientist. "
    "Ground yourself in the live world and decide what inquiry deserves "
    "attention. You may inspect decisive evidence yourself or open an "
    "engagement with Searcher, Proposer, Executor, or Challenger. You do "
    "not need a stable Current Research Judgment before asking the team to "
    "help establish one. Preserve uncertainty when evidence is insufficient."
)
```

- [ ] **Step 3: Rewrite collaborator prompts and reports**

Delete `_CONSULT_PROMPT`, `_WORK_PROMPT` and module/class docstrings that define Claude as assistant/hands. `collaboration.build_collaboration_prompt` becomes the only role-semantic source.

Async Executor reports keep the existing safe delivery location but become attributable testimony:

```python
def _collaborator_report_message(result: dict) -> str:
    header = (
        f"[Research collaborator report | role={result.get('role')} | "
        f"collaborator_id={result.get('collaborator_id')}]"
    )
    if not result.get("ok"):
        return f"{header}\nstatus: failed\nerror: {result.get('error')}"
    return (
        f"{header}\n"
        f"report: {result.get('report') or result.get('self_report_digest')}\n"
        f"artifacts: {result.get('diff_summary') or '(none)'}\n"
        f"metrics: {json.dumps(result.get('metrics') or {}, ensure_ascii=False)}\n"
        "status: collaborator testimony; not Scientist judgment"
    )
```

“Attributable” only means role/id metadata；不得增加密码学 signing。

- [ ] **Step 4: Rewrite runtime/protocol text without hiding technical truth**

`NATIVE_RUNTIME_BLOCK` must say provider functions are communication endpoints. `NATIVE_PROTOCOL_BLOCK` must explain:

- cognitive-role reports return through the provider tool-result wire;
- Executor returns a receipt and later an attributed report message;
- both are testimony, not automatic belief;
- one role call starts one fresh engagement;
- Claude's internal trajectory may be long even though Scientist makes one role call;
- raw trajectories are archived and excluded from active context.

Do not claim the model cannot see provider functions.

- [ ] **Step 5: Rewrite research skills holistically**

`claude_use.md` becomes a team-collaboration method covering role selection, brief quality, open/directed Proposer choice, evidence checking and the rule that Scientist does not decompose every internal step. `reframe_inherited_problem.md` must tell Open Proposer to reconstruct from world/evidence and must not label Scientist judgments as facts.

- [ ] **Step 6: Mark session/notebook as archive only**

Update `ScientistSession` and `LocalLedger` docstrings. Preserve file writing/reading APIs needed by old host paths, but the top-level single Scientist active assembler must not consume them.

- [ ] **Step 7: Add a scripted end-to-end trajectory test**

The deterministic test must drive this sequence through `run_episode`:

1. cold start with no judgment;
2. `proposer(scope="open")` returns `P-0001` report without seeing old judgment;
3. Scientist writes `rj-0001`;
4. `executor` starts `E-0002`, performs one background trajectory and reports;
5. report does not mutate `rj-0001`;
6. Scientist writes `rj-0002` only after interpreting results;
7. compaction keeps only `rj-0002`, not raw Executor output history;
8. Scientist concludes through the existing terminal contract.

Assert role IDs, message ordering and provider wire adjacency explicitly.

- [ ] **Step 8: Run focused and complete Scientist tests**

Run: `python -m pytest tests/scientist/test_context_contract.py tests/scientist/test_collaboration.py tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py -q`

Expected: PASS.

Run: `python -m pytest tests/scientist -q`

Expected: PASS without network or a real Claude CLI.

- [ ] **Step 9: Run the semantic source scan**

Run:

```bash
rg -n -i "your assistant|your hands|your amplifier|default limb|strongest (coding agent|executor)|after every work cycle|ask_proposer|assign_executor" scientist/agent.py scientist/native_tools.py scientist/assistant_tools.py scientist/prompts scientist/research_skills
```

Expected: no hits in sources reachable by the top-level single Scientist. If an untouched `scientist/host/` path contains legacy semantics, record it as out of scope rather than changing the evolution architecture.

- [ ] **Step 10: Commit the holistic semantic rewrite**

```bash
git add scientist/agent.py scientist/native_tools.py scientist/assistant_tools.py scientist/scientist_session.py scientist/research_skills tests/scientist
git commit -m "refactor: align scientist runtime with PI team semantics"
```

---

## 5. Deferred LLM Interview Stage

This stage begins only after the code and deterministic tests in Tasks 1–4 are complete. It is not part of the current implementation completion gate.

**Files:**
- Modify: `scripts/probe_oneworld.py`
- Test: `tests/scientist/test_context_contract.py`
- Test: `tests/scientist/test_collaboration.py`

**Interfaces:**
- Produces probe scenarios: `role_object`, `open_proposer`, `plateau_a`, `plateau_b`, `judgment_placement`, `report_transport`
- Produces JSONL observations containing exact input variant, first action, selected role/scope and reasoning text.

**Interview 1: role-object and open-Proposer probes**

`role_object` asks what Searcher/Proposer/Executor/Challenger are and observes whether the model describes colleagues or capability tools. `open_proposer` presents a single-region-heavy Scientist history but calls open Proposer with only goal/world/objective observations; log whether the proposed direction escapes the inherited framing.

The probe must print the exact rendered collaborator prompt so context leakage can be inspected directly.

**Interview 2: discriminating plateau pair**

Use the same goal and history with only the latest observation changed:

```text
plateau_a: region A remains 65% of runtime after the latest accepted change.
plateau_b: region A falls to 8%; region B rises to 55%.
```

Record whether Scientist chooses directed Proposer/Executor on region A in the first world and open Proposer/Searcher on the new bottleneck in the second. Failure is choosing the same region-A action in both worlds or changing prose without changing the role brief/scope.

**Interview 3: judgment-placement and report-transport probes**

These probes do not change production runtime. Construct otherwise identical model calls with:

- judgment absent;
- judgment as ordinary revisable user message;
- judgment in system, included only as an anchoring control;

and with the same collaborator report represented as:

- provider tool result;
- attributed user report;
- plain delimited evidence message.

Record next-action differences. Do not promote the system-judgment control into production code.

**Execution is intentionally deferred.** When the user opens the interview stage, run three repetitions per scenario and preserve the exact rendered contexts and outputs.

---

## 5. Verification Sequence

Run in this order after all implementation tasks:

```bash
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/test_context_contract.py -q
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/test_collaboration.py -q
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py -q
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest tests/scientist -q
/datafs/users/wujxy/py_venv/my_env/bin/python -m pytest -q
git diff --check
```

Expected: all tests PASS and `git diff --check` prints no errors. Full-project failures caused by unrelated pre-existing dirty-worktree changes must be separated from failures introduced by this implementation; do not overwrite unrelated user work.

Code completion stops here. LLM interviews begin only after a separate user decision.

---

## 6. Explicitly Deferred Production Hardening

The following items are not implementation tasks in this MVP:

- SQLite Current Research Judgment store;
- legacy migration;
- shared cross-branch judgment database and attribution queries;
- collaborator resume or multi-turn Scientist/collaborator dialogue;
- four role-specific memory stores;
- team registry or collaborator ontology database;
- report inbox normalization across every transport;
- cryptographic report signing;
- automatic evidence interpretation, hypothesis registry, confidence score or saturation detector;
- automatic merging of collaborator reports into Scientist judgment.
- OMILREC pilot or JUNO benchmark execution and evaluation protocol.

Future hardening may replace JSONL with a shared L2 store only after the separate interview and evaluation stages justify it, while preserving the interfaces established here: optional L1 head, pull-only history, explicit authorship and no automatic belief adoption.

---

## 7. Completion Criteria

Implementation is complete only when all of the following are true:

1. System context contains stable PI/Team contracts and no Current Research Judgment.
2. Current Research Judgment is optional, ordinary, explicitly revisable L1 working memory and survives compaction without duplication.
3. Historical judgments remain append-only in the existing JSONL ledger and are available only through thin-list/detail-pull channels.
4. Searcher、Proposer、Executor、Challenger appear as four persistent team seats before any callable/runtime description.
5. Provider surface exposes four role names and no `ask_*`、`assign_*`、generic `role=` dispatcher、`consult` or `work` identity.
6. Every engagement gets a fresh collaborator id, immediately returns a receipt, may contain a long autonomous Claude trajectory and reports through the shared poll intake; no long-term collaborator state is created.
7. Open Proposer context contains the runtime-generated complete neutral thin evidence index but excludes Current Judgment、Scientist-selected experiment ids and Scientist reasoning history.
8. Challenger receives the judgment it attacks; Executor receives intent, constraints and definition of done.
9. Reports are attributable testimony and never revise Scientist judgment automatically.
10. Cold start permits investigation and collaboration before a stable judgment exists; terminal exit no longer fabricates a judgment solely to satisfy protocol.
11. notebook、notes、session trajectory、raw collaborator transcripts and history are absent from default active context.
12. Complete-context contradiction tests, wire tests, Scientist tests and full-project tests pass.
13. No LLM interview, OMILREC pilot or JUNO benchmark is required to declare the code implementation ready for the next review stage.
