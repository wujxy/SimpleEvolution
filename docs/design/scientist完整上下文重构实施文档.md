# Scientist Complete Context Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单 Scientist 的完整语义环境重构为“持续 PI + fresh Claude 科研合作者 + canonical Current Research Model”，使劳动轨迹退出 Scientist 的长期活跃上下文，同时保留可审计、可按需回看的认知历史。

**Architecture:** 以最终 assembled context 而不是单个 prompt/tool 为改造和验收单位。Scientist 每轮直接看到 Charter、Research Team Constitution、唯一 Current Research Model、当前 World 与新增 Evidence；Claude collaborators 在 Charter 同级被定义为独立科研团队成员，底层 native tool 仅作为沟通渠道。Current Model 采用一个 active head 加 SQLite append-only revisions，历史默认不注入、通过查询读取；旧 notebook、notes、工作日志继续存档，但不再与 Current Model 竞争 canonical cognition。

**Tech Stack:** Python 3.9+、stdlib JSON/文件存储、provider-native tool calling、pytest。

## Global Constraints

- 本计划只改造单 Scientist 的入世路径：`scientist/` 顶层 package 与 `tests/scientist/test_oneworld.py` 等对应测试。
- 本计划不修改 `scientist/host/`、`supervisor/`、`simpleevo/` 的 Evolution/Supervisor 语义；现在建立可共享的 SQLite model store 和主体/world 归属字段，跨分支查询与注入策略留到后续阶段。
- 不修改 `.env`、密钥、生产配置和 benchmark 产物。
- Context 是一个完整认知环境；任何局部文本或工具改动都必须在最终 assembled context 中复核。
- Claude collaborator 必须在 Research Team Constitution 中先于沟通渠道成立；不得再用 `hands`、`amplifier`、`tool`、`strongest executor` 定义 Claude 的本体地位。
- Scientist 拥有问题选择、belief revision、优先级、转向和结束判断；collaborator 可以独立调查、反驳和执行，但不能直接改写 Current Model。
- Scientist 可以定向读取和做小型 probe 以形成或审计判断；生产性编辑、长 debug 和 measurement campaign 默认由 collaborator 承担。
- Current Research Model 是唯一 canonical cognition；每次模型调用和 compaction 后都必须直接可见。
- Current Model 只在认识或 agenda 改变的 research junction 修订，不能因每次 collaborator 完工而机械更新。
- Model history 必须 append-only、可追溯、默认不进入活跃上下文；raw collaborator transcript 与实验日志只通过引用按需读取。
- Model store 必须由构造参数接收 DB path、`scientist_id` 和 `world_id`；单 Scientist 默认使用 `.scientist/research_models.sqlite3`，以后外层可以传入 run-level shared DB。
- 保持第一性原理和 MVP：不引入 hypothesis registry、confidence 数字、固定认知表格、自动 evidence interpreter 或四套重型 agent framework。
- 新行为必须先写失败测试，再写最小实现；完成后运行相关 pytest 和完整 `python -m pytest tests/scientist -q`。

---

## 1. 设计合同：最终上下文必须表达同一套世界观

### 1.1 单 Scientist 的主体关系

最终上下文必须按以下顺序构造：

```text
Scientist Identity / Charter
Research Team Constitution
Current Research Model
Current World and Evidence
Research methods
Team communication channels
World-contact and memory channels
Protocol and filesystem boundaries
```

这些标题不是装饰。顺序表达了本体优先级：Scientist 和科研团队先存在，随后才出现它们使用的沟通渠道与世界接触渠道。

### 1.2 不可互相否认的语义公理

1. Scientist 是持续负责整个课题的 PI，不是一次任务、一次实现或一次 lease 的执行者。
2. Fresh Claude collaborators 是独立科研团队成员，不是 Scientist 的手脚、工具或能力扩展。
3. Scientist 不需要先掌握全部实现细节才能邀请 collaborator 共同研究。
4. Collaborator 可以重新界定子问题、反驳 Scientist、报告“当前问题问错了”。
5. Scientist 对最终科研判断负责，不等于重复 collaborator 的全部劳动；Scientist 审计关键证据。
6. 局部实验成功只是 evidence，不自动意味着课题完成；collaborator 完工也不自动触发 belief revision。
7. Current Model 是“我现在相信什么”；历史 revision 是“我是怎样走到这里的”；raw archive 是“必要时可以重新审判的材料”。
8. Tool schema 只能描述通信或接触动作，不能在后文把 Claude 重新降格成工具。

### 1.3 本轮明确不做

- 不让 Harness 解释 evidence、维护 hypothesis 或决定饱和。
- 不自动把 collaborator report 合并进 Current Model。
- 不把全部 model history、notebook、notes 或 session trajectory 推回 system prompt。
- 不实现跨 Scientist/跨分支 model 搜索；只预留稳定 model id、world id、scientist id 的存储兼容方向。
- 不要求所有 Searcher/Proposer/Challenger 都成为长驻进程；本轮只把它们作为 fresh collaborator 的角色语义。

---

## 2. 文件结构与职责边界

### 新建

- `scientist/prompts/scientist.md`：单 Scientist PI Charter；不再复用 seat/proposer Charter。
- `scientist/prompts/research_team.md`：与 Charter 同级的 Research Team Constitution。
- `scientist/research_model_store.py`：SQLite Current Model head/revision store；只做存储和归属过滤，不解释模型内容。
- `tests/scientist/test_context_contract.py`：对最终 assembled context 做顺序、正向公理和矛盾词审计。
- `tests/scientist/test_research_model_store.py`：SQLite revision、主体/world 隔离、legacy import 与薄历史索引。

### 修改

- `scientist/agent.py`：整体 context assembler、动态 Current Model 注入、collaborator report 消息、nudge/cold-start/checkpoint/compaction 语义。
- `scientist/native_tools.py`：把通用 tool block 重组为 team communication、world contact、research memory 三组 channel；暴露新语义名称。
- `scientist/assistant_tools.py`：保持 Claude CLI 与 raw transcript 机制，改变外部交互语义和署名报告；避免为代码重命名而做大规模机械重构。
- `scientist/ledger.py`：继续保存 notes/experiment/archive；将 Current Model 操作委托给 `ResearchModelStore`，并提供旧调用兼容入口。
- `scientist/cli.py`：恢复时不再把 notebook/notes 作为当前认知注入；为每次模型调用提供动态 assembled context。
- `scientist/research_skills/claude_use.md`：改写为团队协作方法，删除 hands/amplifier/“Scientist 重做全部验证”等冲突表达。
- `scientist/research_skills/reframe_inherited_problem.md`：用 Current Model/World/evidence 语义复核，不继承其他主体的 cognition。
- `tests/scientist/test_oneworld.py`：Current Model、动态重锚、collaborator report、compaction、cutoff checkpoint 测试。
- `tests/scientist/test_model_native_tools.py`：新 channel 名称与 provider-native wire contract。

### 保留但不再作为活跃认知

- `.scientist/session/session.jsonl`：完整审计档案。
- `.scientist/session/notebook.md`：legacy autobiography，只为旧 run 可读；单 Scientist 新路径不再注入 system prompt。
- `.scientist/notes.md`：legacy/人工工作记录，只存档，不再进入 standing context。
- `.scientist/assistant/<call_id>/`：raw collaborator transcript 与 digest，目录名本轮不迁移，避免破坏已有 run。

---

### Task 1: 建立完整 assembled-context 合同测试

**Files:**
- Create: `scientist/prompts/scientist.md`
- Create: `scientist/prompts/research_team.md`
- Create: `tests/scientist/test_context_contract.py`
- Modify: `scientist/agent.py`

**Interfaces:**
- Consumes: `build_system_prompt(spec, *, current_model=None, roots=None) -> str`
- Produces: 一组在后续所有 task 中持续生效的整体语义回归测试。

- [ ] **Step 1: 写最终上下文顺序的失败测试**

```python
from scientist.agent import build_system_prompt


def _prompt(current_model=None):
    return build_system_prompt(
        {"goal": "understand and improve the system",
         "editable_paths": ["src"]},
        current_model=current_model,
        roots={"work": "/work", "repo": "/repo", "scratch": "/scratch"},
    )


def test_complete_context_has_one_semantic_order():
    text = _prompt({
        "research_model_id": "rm-0002",
        "current_model": "Memory lifetime is the leading explanation.",
        "revision_reason": "E2 weakened the arithmetic explanation.",
        "evidence_refs": ["experiment:E2"],
    })
    headings = [
        "# Scientist Charter",
        "# Research Team Constitution",
        "# Current Research Model",
        "# Current World and Evidence",
        "# Team Communication Channels",
        "# World Contact and Research Memory Channels",
    ]
    positions = [text.index(item) for item in headings]
    assert positions == sorted(positions)
```

- [ ] **Step 2: 写无 Current Model 时不伪造认知的失败测试**

```python
def test_no_current_model_is_explicit_not_fabricated():
    text = _prompt()
    block = text.split("# Current Research Model", 1)[1]
    assert "No Current Research Model is on file yet" in block
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: FAIL，因为当前没有 `current_model` 参数、Research Team Constitution 和新的 channel 标题。

- [ ] **Step 4: 写入最小 PI/Team standing blocks**

`scientist/prompts/scientist.md`：

```markdown
# Scientist Charter

You are the persistent Scientist responsible for this research topic. You
own scientific judgment: what you currently believe, what matters now, and
what the research should do next.
```

`scientist/prompts/research_team.md`：

```markdown
# Research Team Constitution

You work with a research team of independent fresh Claude collaborators.
Their signed reports are testimony; only your judgment may revise your
Current Research Model.
```

- [ ] **Step 5: 在 `scientist/agent.py` 中搭出新的 assembler 接口**

先把现有拼装拆成显式 block；Task 3 再完整审计所有后置文字：

```python
def render_current_model(model: dict | None) -> str:
    if not model:
        body = (
            "No Current Research Model is on file yet. Form an initial "
            "model before making a consequential research commitment."
        )
    else:
        body = str(model["current_model"]).strip()
        refs = model.get("evidence_refs") or []
        if refs:
            body += "\n\nEvidence refs: " + ", ".join(refs)
    return "# Current Research Model\n\n" + body


def build_system_prompt(spec: dict, *, current_model: dict | None = None,
                        roots: dict | None = None) -> str:
    roots = roots or {}
    world = build_generation_context(
        goal=spec.get("goal") or "(no goal stated)",
        editable=list(spec.get("editable_paths") or []),
        frozen=[], base_sha=spec.get("base_sha") or "—" * 40,
        gate_block=spec.get("gate_block") or "(no gates stated)",
    )
    team_channels = (
        "# Team Communication Channels\n\n" + NATIVE_TOOL_BLOCK
    )
    world_channels = (
        "# World Contact and Research Memory Channels\n\n"
        "The native schemas above are the temporary channel surface; "
        "Task 3 splits and renames them without changing this block order."
    )
    return "\n\n".join([
        load_semantic("scientist").strip(),
        load_semantic("research_team").strip(),
        render_current_model(current_model),
        "# Current World and Evidence\n\n" + world,
        team_channels,
        world_channels,
        NATIVE_PROTOCOL_BLOCK,
        render_native_boundaries(
            str(roots.get("work") or "/work"),
            str(roots.get("repo") or "/repo"),
            str(roots.get("scratch") or "/scratch"),
        ),
    ])
```

- [ ] **Step 6: 运行新增结构测试**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: PASS。Task 3 会先增加完整语义与矛盾测试，再替换 temporary channel surface。

- [ ] **Step 7: 提交**

```bash
git add scientist/prompts/scientist.md scientist/prompts/research_team.md scientist/agent.py tests/scientist/test_context_contract.py
git commit -m "test: define complete scientist context contract"
```

---

### Task 2: 把 ResearchState 收敛为 canonical Current Research Model

**Files:**
- Create: `scientist/research_model_store.py`
- Modify: `scientist/ledger.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/agent.py`
- Create: `tests/scientist/test_research_model_store.py`
- Test: `tests/scientist/test_oneworld.py`
- Test: `tests/scientist/test_model_native_tools.py`

**Interfaces:**
- Produces: `ResearchModelStore(db_path, *, scientist_id, world_id)`。
- Produces: `LocalLedger.revise_current_model(action: dict) -> dict`，委托给 store。
- Produces: `LocalLedger.current_model() -> dict | None`，只返回当前主体/current world 的 head。
- Produces: `LocalLedger.model_history(limit: int = 20) -> list[dict]`。
- Produces: `LocalLedger.inspect_model_revision(model_id: str) -> dict`。
- Produces native channels: `revise_current_model`, `inspect_model_history`, `inspect_model_revision`
- Compatibility: 旧 `working_model` rows 继续可读；旧 `update_research_state` 不再向新模型暴露，但 dispatch 保留兼容 alias。

- [ ] **Step 1: 写 Current Model revision 的失败测试**

```python
def test_revise_current_model_creates_one_head_and_history(tmp_path):
    store = ResearchModelStore(
        tmp_path / "models.sqlite3", scientist_id="s1", world_id="w1",
    )
    first = store.revise_current_model({
        "current_model": "FCN arithmetic is the leading explanation.",
        "revision_reason": "Initial grounding.",
        "evidence_refs": ["source:profile-1"],
    })
    second = store.revise_current_model({
        "current_model": "State lifetime is now the leading explanation.",
        "revision_reason": "E2 weakened the FCN arithmetic model.",
        "evidence_refs": ["experiment:E2"],
    })
    assert first["research_model_id"] == "rm:s1:w1:0001"
    assert second["research_model_id"] == "rm:s1:w1:0002"
    assert store.current_model()["current_model"].startswith("State lifetime")
    assert store.current_model()["supersedes_model_id"] == "rm:s1:w1:0001"
    assert [row["research_model_id"] for row in store.model_history()] == [
        "rm:s1:w1:0002", "rm:s1:w1:0001",
    ]
    assert "current_model" not in store.model_history()[0]
```

- [ ] **Step 2: 写 legacy `working_model` row 的失败测试**

```python
def test_legacy_research_state_is_imported_once(tmp_path):
    legacy = tmp_path / "research_state.jsonl"
    legacy.write_text(
        '{"research_state_id":"rs-0003","revision":3,'
        '"working_model":"legacy model","evidence_refs":["x"]}\n',
        encoding="utf-8",
    )
    store = ResearchModelStore(
        tmp_path / "models.sqlite3", scientist_id="s1", world_id="w1",
    )
    store.import_legacy_jsonl(legacy)
    store.import_legacy_jsonl(legacy)
    current = store.current_model()
    assert current["research_model_id"] == "legacy:s1:w1:rs-0003"
    assert current["current_model"] == "legacy model"
    assert current["revision_reason"] == "legacy revision; reason unavailable"
    assert len(store.model_history()) == 1
```

- [ ] **Step 3: 写 history 查询 channel 的失败测试**

```python
def test_model_history_is_thin_and_revision_is_pull_only(tmp_path):
    store = ResearchModelStore(
        tmp_path / "models.sqlite3", scientist_id="s1", world_id="w1",
    )
    store.revise_current_model({
        "current_model": "model body", "revision_reason": "new evidence",
        "evidence_refs": ["experiment:E1"],
    })
    index = store.model_history()
    assert index == [{
        "research_model_id": "rm:s1:w1:0001", "revision": 1,
        "revision_reason": "new evidence",
        "evidence_refs": ["experiment:E1"],
        "supersedes_model_id": None,
    }]
    detail = store.inspect_model_revision("rm:s1:w1:0001")
    assert detail["current_model"] == "model body"
```

- [ ] **Step 4: 写同库不同主体/world 默认隔离的失败测试**

```python
def test_shared_db_scopes_heads_by_scientist_and_world(tmp_path):
    path = tmp_path / "shared.sqlite3"
    a = ResearchModelStore(path, scientist_id="s1", world_id="w1")
    b = ResearchModelStore(path, scientist_id="s2", world_id="w2")
    a.revise_current_model({
        "current_model": "model A", "revision_reason": "ground A",
        "evidence_refs": [],
    })
    b.revise_current_model({
        "current_model": "model B", "revision_reason": "ground B",
        "evidence_refs": [],
    })
    assert a.current_model()["current_model"] == "model A"
    assert b.current_model()["current_model"] == "model B"
    assert len(a.model_history()) == 1
    assert len(b.model_history()) == 1
```

- [ ] **Step 5: 运行测试并确认失败**

Run: `python -m pytest tests/scientist/test_research_model_store.py -q`

Expected: FAIL with missing `scientist.research_model_store`。

- [ ] **Step 6: 建立 SQLite schema 与 scope**

`ResearchModelStore.__init__` 创建下表；所有读写必须带 `scientist_id` 与 `world_id` 条件：

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS research_models (
    research_model_id TEXT PRIMARY KEY,
    scientist_id TEXT NOT NULL,
    world_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    current_model TEXT NOT NULL,
    revision_reason TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    supersedes_model_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(scientist_id, world_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_research_models_scope_revision
ON research_models(scientist_id, world_id, revision DESC);
"""
```

使用 `sqlite3.connect` 每次打开短连接；写 revision 使用 `BEGIN IMMEDIATE`，在同一事务内读取当前 head、分配下一 revision 并插入，避免以后共享库并发产生重复 revision。

- [ ] **Step 7: 实现 revision、head、薄索引和按 id pull**

`revise_current_model` 的核心写入必须是：

```python
with self._connect() as connection:
    connection.execute("BEGIN IMMEDIATE")
    previous = connection.execute(
        "SELECT research_model_id, revision FROM research_models "
        "WHERE scientist_id=? AND world_id=? ORDER BY revision DESC LIMIT 1",
        (self.scientist_id, self.world_id),
    ).fetchone()
    revision = (int(previous["revision"]) + 1) if previous else 1
    model_id = (
        f"rm:{self.scientist_id}:{self.world_id}:{revision:04d}"
    )
    connection.execute(
        "INSERT INTO research_models VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (model_id, self.scientist_id, self.world_id, revision,
         current_model, revision_reason,
         json.dumps(refs, ensure_ascii=False),
         previous["research_model_id"] if previous else None,
         datetime.now(timezone.utc).isoformat()),
    )
```

`model_history` 的 SELECT 不得选择 `current_model`；`inspect_model_revision` 必须同时限制当前 `scientist_id/world_id`，本轮不开放跨主体读取。

- [ ] **Step 8: 实现幂等 legacy import**

`import_legacy_jsonl(path)` 只在当前 scope 没有 revision 时导入有效旧 rows；ID 使用 `legacy:<scientist_id>:<world_id>:<research_state_id>`，`working_model` 映射为 `current_model`，reason 固定为 `legacy revision; reason unavailable`。导入在单一事务中执行，第二次调用不得产生重复行。

- [ ] **Step 9: 让 LocalLedger 委托 model store**

```python
def __init__(self, root: Path, *, scientist_id: str = "local",
             world_id: str = "world", model_db_path: Path | None = None):
    self.root = Path(root)
    self.research_state_path = self.root / "research_state.jsonl"
    self.models = ResearchModelStore(
        model_db_path or self.root / "research_models.sqlite3",
        scientist_id=scientist_id, world_id=world_id,
    )
    self.models.import_legacy_jsonl(self.research_state_path)


def revise_current_model(self, action: dict) -> dict:
    return self.models.revise_current_model(action)
```

- [ ] **Step 10: 增加新 native channel schema 与 dispatch**

`revise_current_model` 只接受三个 Scientist-authored 字段：

```python
REVISE_CURRENT_MODEL_CHANNEL = _fn(
    "revise_current_model",
    "Revise your one canonical Current Research Model only when evidence "
    "or scientific judgment changes what you believe or what matters now.",
    {
        "current_model": {"type": "string"},
        "revision_reason": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
    },
    ["current_model", "revision_reason"],
)
```

`inspect_model_history` 返回薄索引，`inspect_model_revision` 才返回正文。删除新 schema 中的 `experiment_log`、`deliverables`、`conclusion`；它们继续存在于各自事实/终止记录中。

- [ ] **Step 11: 保留旧入口的读取兼容，不把旧名字重新暴露给模型**

```python
if name == "revise_current_model":
    return ledger.revise_current_model(action)
if name == "update_research_state":  # legacy replay only
    return ledger.update_research_state(action)
```

- [ ] **Step 12: 运行相关测试**

Run: `python -m pytest tests/scientist/test_research_model_store.py tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py -q`

Expected: PASS。

- [ ] **Step 13: 提交**

```bash
git add scientist/research_model_store.py scientist/ledger.py scientist/native_tools.py scientist/agent.py tests/scientist/test_research_model_store.py tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py
git commit -m "feat: make current research model canonical"
```

---

### Task 3: 整体重写 Charter、Team Constitution、cold start、skills 与 channels

**Files:**
- Modify: `scientist/prompts/scientist.md`
- Modify: `scientist/prompts/research_team.md`
- Modify: `scientist/agent.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/research_skills/claude_use.md`
- Modify: `scientist/research_skills/reframe_inherited_problem.md`
- Test: `tests/scientist/test_context_contract.py`

**Interfaces:**
- Consumes: Task 1 的 assembled-context order。
- Produces: 无内部矛盾的完整 system prompt。
- Produces channels: `ask_collaborator`, `start_investigation`, `wait_for_collaborator`。

- [ ] **Step 1: 扩充失败测试，覆盖所有后置语义表面**

```python
def test_later_blocks_preserve_pi_and_collaborator_semantics():
    text = _prompt().lower()
    required = (
        "persistent scientist", "research team",
        "independent research collaborators",
        "scientific judgment", "local success is evidence",
        "audit critical evidence",
    )
    assert all(phrase in text for phrase in required)
    forbidden = (
        "seat charter", "your lens is your identity", "deliver your world",
        "your assistant", "your hands", "your amplifier",
        "after every work cycle", "do production yourself",
    )
    assert all(phrase not in text for phrase in forbidden)
```

- [ ] **Step 2: 运行测试并确认旧 prompt、skills、nudge 产生失败**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: FAIL，输出包含 `Seat Charter`、`your assistant`、`hands` 等旧语义。

- [ ] **Step 3: 写新的 PI Charter**

`scientist/prompts/scientist.md` 必须覆盖责任和边界，不写 Persona 形容词：

```markdown
# Scientist Charter

You are the persistent Scientist responsible for this research topic. The
user gives the final value and hard constraints; you own the evolving
scientific questions beneath them. Local success is evidence about the topic,
not automatic completion of it: a completed investigation or a working
implementation does not end the research by itself.

Your irreducible responsibility is to decide what you currently believe,
what matters now, and what the research should do next. You form and revise
the Current Research Model, choose consequential questions, interpret
evidence, and decide whether to continue, turn, pause, or conclude.

You remain in contact with reality: read the decisive source, inspect
surprising measurements, and audit critical evidence. Responsibility does
not require reproducing every implementation or debugging step yourself.
```

- [ ] **Step 4: 写与 Charter 同级的 Research Team Constitution**

`scientist/prompts/research_team.md` 必须先定义团队关系，再定义角色：

```markdown
# Research Team Constitution

You lead a research team of fresh Claude collaborators. They are independent
research collaborators, not tools, hands, or extensions of you. You may
invite a collaborator before you know every implementation detail. Give the
question, why it matters, the relevant constraints, and what evidence would
change the decision; the collaborator independently plans its investigation.

Searcher investigates literature and facts. Proposer develops directions
inside a question or research region. Executor carries implementation,
debugging, measurement, and self-verification through to a report.
Challenger attacks your current interpretation and supplies alternatives.

A collaborator may reject your framing or report that the question is wrong.
Its signed report is testimony, not your belief. Only your own judgment may
revise the Current Research Model.
```

- [ ] **Step 5: 用新的整体顺序组装 context**

在 `build_system_prompt` 中加载 `scientist.md` 与 `research_team.md`，不再加载 `proposer.md`：

```python
parts = [
    load_semantic("scientist"),
    load_semantic("research_team"),
    render_current_model(current_model),
    "# Current World and Evidence\n\n" + world,
    startup_block,
    skill_block,
    TEAM_COMMUNICATION_CHANNELS_BLOCK,
    WORLD_AND_MEMORY_CHANNELS_BLOCK,
    NATIVE_PROTOCOL_BLOCK,
    boundaries,
]
```

删除 `seat_identity_block`、`_SCIENTIST_MODE` 在单 Scientist path 中的注入。若 spec 带有 `lens`，只在 World block 中标成用户提供的 research perspective，不把它写成 Scientist identity。

- [ ] **Step 6: 改写 cold start 与全部 nudge**

新的 cold start 只要求 Scientist grounding、形成 Current Model、判断是否邀请 collaborator；预算 nudge 只报告资源，不以“build/deliver”替代科研判断：

```python
_COLD_START = (
    "Begin by grounding yourself in the current world and forming your own "
    "initial Current Research Model. Decide which uncertainty matters first "
    "and whether a fresh collaborator should investigate it with you."
)

_BUDGET_NUDGE = (
    "Your research time is nearing its budget. Reassess the Current Research "
    "Model against the evidence now on file. Conclude only if your scientific "
    "judgment supports completion or saturation; otherwise leave the most "
    "truthful current model on file before the cutoff."
)
```

- [ ] **Step 7: 重组 native channels，不再输出统一的 generic tool 分区**

暴露以下名称：

```text
Team Communication Channels
  ask_collaborator(role=searcher|proposer|challenger, question, context, read)
  start_investigation(question, context, workspace=shared|fresh, timeout_minutes)
  wait_for_collaborator(timeout_seconds)

World Contact and Research Memory Channels
  read_file
  bash
  revise_current_model
  inspect_model_history
  inspect_model_revision
  search_experiments
  inspect_experiment
  inspect_originating_research_state
  use_research_skill
```

从单 Scientist 暴露面移除 `write_file`；`bash` 描述限定为小型 probe、测量和关键审计，不鼓励生产性编辑、长 debug 或完整 campaign。

- [ ] **Step 8: 改写 collaboration skill 与 reframe skill**

`claude_use.md` 改名可后续处理，本轮保留 skill id 兼容；正文必须使用“colleague/collaborator/team”，并明确：

```markdown
- Invite a collaborator with a scientific question, not a command transcript.
- Do not pre-solve every implementation detail before collaborating.
- Ask for independent alternatives and allow the collaborator to reject the framing.
- Audit decisive evidence; do not duplicate the collaborator's entire execution history.
- A report changes the Current Research Model only after your judgment.
```

- [ ] **Step 9: 运行完整 context contract**

Run: `python -m pytest tests/scientist/test_context_contract.py -q`

Expected: PASS，包括顺序、required phrases 和 forbidden phrases。

- [ ] **Step 10: 提交**

```bash
git add scientist/prompts/scientist.md scientist/prompts/research_team.md scientist/agent.py scientist/native_tools.py scientist/research_skills/claude_use.md scientist/research_skills/reframe_inherited_problem.md tests/scientist/test_context_contract.py
git commit -m "feat: establish scientist PI and research team context"
```

---

### Task 4: 让 collaborator 作为署名主体来信，而不是普通结果内容

**Files:**
- Modify: `scientist/assistant_tools.py`
- Modify: `scientist/agent.py`
- Modify: `scientist/native_tools.py`
- Test: `tests/scientist/test_oneworld.py`
- Test: `tests/scientist/test_model_native_tools.py`

**Interfaces:**
- `ask_collaborator(action) -> receipt dict`：read-only fresh collaborator，报告进入 ready inbox。
- `start_investigation(action) -> receipt dict`：async fresh collaborator，报告稍后进入 inbox。
- `poll() -> list[dict]`：唯一 report intake。
- 机械 tool result 只返回 receipt；报告正文作为 `[Claude collaborator <collaborator_id> reports]` 独立 user message到达。

- [ ] **Step 1: 写同步与异步 collaborator 都通过独立消息到达的失败测试**

```python
def test_ask_collaborator_returns_receipt_then_signed_report(tmp_path):
    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    team = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path))),
        ledger=ledger, episode_id="t",
    )
    receipt = team.ask_collaborator({
        "role": "challenger", "question": "Attack the cache model.",
        "context": "E1 improved local time only.", "read": "none",
    })
    assert set(receipt) == {"ok", "collaborator_id", "status"}
    assert receipt["status"] == "report_ready"
    reports = team.poll()
    assert len(reports) == 1
    message = _collaborator_report_message(reports[0])
    assert "role: challenger" in message
    assert "signed report; not your belief" in message
```

- [ ] **Step 2: 运行测试并确认当前 `consult` 把 digest 直接塞进 tool result**

Run: `python -m pytest tests/scientist/test_oneworld.py -q`

Expected: FAIL，因为当前同步 consult 返回 `answer_digest`，且 header 是 `your assistant finished`。

- [ ] **Step 3: 在现有 `InWorldAssistant` 内增加 ready-report inbox**

不做大规模类/文件重命名，只改变对模型可见的关系：

```python
# In InWorldAssistant.__init__, next to self._jobs:
if not hasattr(self, "_ready_reports"):
    self._ready_reports: list[dict] = []


def ask_collaborator(self, action: dict) -> dict:
    role = str(action.get("role") or "").strip()
    result = self.consult({
        "question": action.get("question"),
        "context": action.get("context"),
        "read": action.get("read", "none"),
    })
    collaborator_id = result.get("call_id")
    self._ready_reports.append({
        **result, "collaborator_id": collaborator_id,
        "role": role, "kind": "inquiry",
    })
    return {"ok": result.get("ok", False),
            "collaborator_id": collaborator_id,
            "status": "report_ready"}


def poll(self) -> list[dict]:
    reports, self._ready_reports = self._ready_reports, []
    reports.extend(self._poll_investigations())
    return reports
```

- [ ] **Step 4: 将 `work` 外部语义映射为 `start_investigation`**

内部 `_Job` 和 raw archive 可保留；receipt 与 report 必须携带 `collaborator_id`、`role=executor`，prompt 用“independent collaborator investigating a research question”，不使用 hands/tool/work package。

- [ ] **Step 5: 改写独立报告消息**

```python
def _collaborator_report_message(result: dict) -> str:
    collaborator_id = result.get("collaborator_id") or result.get("call_id", "?")
    return (
        f"[Claude collaborator {collaborator_id} reports]\n"
        f"role: {result.get('role', 'collaborator')}\n"
        f"judgment: {result.get('answer_digest') or result.get('self_report_digest')}\n"
        f"artifacts: {result.get('diff_summary') or '(none)'}\n"
        f"metrics: {json.dumps(result.get('metrics') or {}, ensure_ascii=False)}\n"
        "(signed report; not your belief — judge what, if anything, changes "
        "your Current Research Model)"
    )
```

- [ ] **Step 6: 保持 native wire invariant**

报告只能在 tool result 已紧跟其 assistant tool-call message 后，由下一轮顶部的唯一 pump 注入；不得在 `ask_collaborator` dispatch 中直接 append user message。

- [ ] **Step 7: 运行 async/wait/wire tests**

Run: `python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py -q`

Expected: PASS；既有“tool result 不孤立”测试继续通过，新报告 header 通过。

- [ ] **Step 8: 提交**

```bash
git add scientist/assistant_tools.py scientist/agent.py scientist/native_tools.py tests/scientist/test_oneworld.py tests/scientist/test_model_native_tools.py
git commit -m "feat: deliver signed collaborator reports"
```

---

### Task 5: 每轮与 compaction 后重新锚定 Current Model

**Files:**
- Modify: `scientist/agent.py`
- Modify: `scientist/cli.py`
- Modify: `scientist/scientist_session.py`
- Test: `tests/scientist/test_oneworld.py`
- Test: `tests/scientist/test_scientist_session.py`

**Interfaces:**
- Existing `run_episode` signature changes only `system_prompt` to `str | Callable[[], str]`; all other parameters remain unchanged.
- CLI 提供 `lambda: build_system_prompt(spec, current_model=ledger.current_model(), roots=roots)`。
- CLI 在构造 `LocalLedger` 时传入 `session.scientist_id`、current world identity 和可选 `spec["research_model_db"]`。
- session/notebook 保留 legacy 读取，但不进入单 Scientist active context。

- [ ] **Step 1: 写模型修订后下一轮 system 立即变化的失败测试**

```python
def test_current_model_is_reanchored_after_revision_and_compaction(tmp_path):
    class ScriptedModel:
        def __init__(self):
            self.systems = []
            self.turn = 0

        def complete(self, *, system, messages, timeout_seconds, tools):
            self.systems.append(system)
            self.turn += 1
            if self.turn == 1:
                return ModelReply(tool_calls=(ToolCall(
                    id="m1", name="revise_current_model", arguments={
                        "current_model": "State lifetime is primary.",
                        "revision_reason": "Initial grounding.",
                        "evidence_refs": [],
                    }),))
            return ModelReply(tool_calls=(ToolCall(
                id="x1", name="abstain",
                arguments={"reason": "test", "axes_checked": ["test"]},
            ),))

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path))),
        ledger=ledger, episode_id="t",
    )
    model = ScriptedModel()
    system_prompt = lambda: build_system_prompt(
        {"goal": "g", "editable_paths": ["src"]},
        current_model=ledger.current_model(),
    )
    run_episode(
        model=model, system_prompt=system_prompt,
        messages=[{"role": "user", "content": "begin"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=2, wall_seconds=30,
        compact_keep_messages=2, compact_max_chars=100,
    )
    assert "No Current Research Model is on file yet" in model.systems[0]
    assert "State lifetime is primary" in model.systems[1]
```

- [ ] **Step 2: 写 resume 不注入 notebook/notes 的失败测试**

```python
def test_notebook_and_notes_are_archive_not_canonical_context(tmp_path):
    ledger = LocalLedger(tmp_path / ".scientist")
    ledger.append_note("legacy note claim")
    ledger.revise_current_model({
        "current_model": "canonical model",
        "revision_reason": "initial grounding",
        "evidence_refs": [],
    })
    spec = {"goal": "g", "editable_paths": ["src"]}
    prompt = build_system_prompt(spec, current_model=ledger.current_model())
    assert "legacy notebook claim" not in prompt
    assert "legacy note claim" not in prompt
    assert "canonical model" in prompt
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_scientist_session.py -q`

Expected: FAIL，因为 system prompt 当前是一次性字符串，CLI 仍注入 notebook/notes。

- [ ] **Step 4: 让 `run_episode` 支持动态 system prompt factory**

```python
from collections.abc import Callable


def _system_text(system_prompt: str | Callable[[], str]) -> str:
    return system_prompt() if callable(system_prompt) else system_prompt


# 每次 model.complete 前调用，而不是 run 开始时冻结：
reply = model.complete(
    system=_system_text(system_prompt),
    messages=messages,
    timeout_seconds=remaining,
    tools=list(NATIVE_TOOLS),
)
```

checkpoint 也必须调用 `_system_text(system_prompt)`，不能拿旧字符串。

- [ ] **Step 5: CLI 只以 Current Model 构造 canonical context**

```python
session = ScientistSession._load_from_dir(
    session_dir, PROMPT_VERSION, episode_id=episode_id,
)
model_db = spec.get("research_model_db")
ledger = LocalLedger(
    ledger_root,
    scientist_id=session.scientist_id,
    world_id=str(spec.get("node_id") or spec.get("base_sha") or world.work),
    model_db_path=Path(model_db) if model_db else None,
)


def active_system_prompt() -> str:
    return build_system_prompt(
        spec, current_model=ledger.current_model(), roots=roots,
    )

result = run_episode(
    model=model,
    system_prompt=active_system_prompt,
    messages=_opening_messages(spec),
    world=world, assistant=assistant, ledger=ledger,
    steps_budget=int(budget.get("steps", 200)),
    wall_seconds=float(budget.get("wall_seconds", 3600)),
    session=session,
    compact_keep_messages=int(budget.get("compact_keep_messages", 400)),
    compact_max_chars=int(budget.get("compact_max_chars", 200_000)),
)
```

删除单 Scientist CLI 对 `session.notebook` 和 `ledger.read_notes()` 的 system-prompt 注入。文件继续存在，旧 run 不丢数据。

- [ ] **Step 6: 明确 session archive 与 canonical cognition 的边界**

更新 `ScientistSession` docstring：`session.jsonl` 是审计档案，`notebook.md` 是 legacy autobiography；单 Scientist active cognition 由 ledger Current Model 提供。不要删除 host 仍使用的方法。

- [ ] **Step 7: 运行动态 context、compaction 和 session tests**

Run: `python -m pytest tests/scientist/test_oneworld.py tests/scientist/test_scientist_session.py -q`

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add scientist/agent.py scientist/cli.py scientist/scientist_session.py tests/scientist/test_oneworld.py tests/scientist/test_scientist_session.py
git commit -m "feat: reanchor current model on every scientist turn"
```

---

### Task 6: 用 Current Model checkpoint 替代 notebook 自我竞争

**Files:**
- Modify: `scientist/agent.py`
- Modify: `scientist/cli.py`
- Test: `tests/scientist/test_oneworld.py`

**Interfaces:**
- Produces: `_current_model_checkpoint(model, system_prompt, messages, ledger, deadline, usages) -> None`
- Cutoff 时 best-effort 写入一条新的 model revision；不再写 `notebook.md`。

- [ ] **Step 1: 写 cutoff checkpoint 的失败测试**

```python
def test_cutoff_checkpoint_revises_current_model_not_notebook(tmp_path):
    # First call consumes the only normal step; checkpoint returns JSON.
    checkpoint_reply = ModelReply(text=json.dumps({
        "current_model": "The best current explanation at cutoff.",
        "revision_reason": "Budget cutoff checkpoint.",
        "evidence_refs": ["collaborator:C1"],
    }))

    class ScriptedCheckpointModel:
        def __init__(self, checkpoint):
            self.calls = 0
            self.checkpoint = checkpoint

        def complete(self, *, system, messages, timeout_seconds, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(text="The investigation is still open.")
            return self.checkpoint

    world = _world(tmp_path)
    ledger = LocalLedger(world.work / ".scientist")
    assistant = InWorldAssistant(
        world=world,
        config=AssistantConfig(command=str(_fake_claude(tmp_path))),
        ledger=ledger, episode_id="t",
    )
    session = ScientistSession._load_from_dir(
        tmp_path / "session", "test-v1", episode_id="t",
    )
    model = ScriptedCheckpointModel(checkpoint_reply)
    result = run_episode(
        model=model, system_prompt=lambda: "system",
        messages=[{"role": "user", "content": "begin"}],
        world=world, assistant=assistant, ledger=ledger,
        steps_budget=1, wall_seconds=30, session=session,
    )
    assert result["outcome"] == "cut_off"
    assert ledger.current_model()["current_model"].startswith("The best")
    assert not session.notebook_path.exists()
```

- [ ] **Step 2: 运行测试并确认当前 `_notebook_checkpoint` 写 notebook**

Run: `python -m pytest tests/scientist/test_oneworld.py -q`

Expected: FAIL。

- [ ] **Step 3: 改写 checkpoint prompt**

```python
_CURRENT_MODEL_CHECKPOINT_PROMPT = (
    "The run is being cut off. Return one JSON object preserving your own "
    "best current scientific judgment, not a work diary: "
    '{"current_model":"best current scientific judgment",'
    '"revision_reason":"Budget cutoff checkpoint.",'
    '"evidence_refs":["experiment:E1"]}. '
    "Do not claim facts not present in evidence."
)
```

- [ ] **Step 4: 校验并写入 ledger**

只在 JSON 完整、`current_model` 非空时调用 `ledger.revise_current_model`；checkpoint 失败不得覆盖已有 head，也不得改变 cutoff outcome。

- [ ] **Step 5: 删除单 Scientist cutoff 对 `session.write_notebook` 的调用**

保留 `ScientistSession.write_notebook` 给未纳入本计划的 host/supervisor 路径使用。

- [ ] **Step 6: 运行 cutoff 与 exit-contract tests**

Run: `python -m pytest tests/scientist/test_oneworld.py -q`

Expected: PASS；deliver/abstain 仍要求至少一个 Current Model revision 在案。

- [ ] **Step 7: 提交**

```bash
git add scientist/agent.py scientist/cli.py tests/scientist/test_oneworld.py
git commit -m "feat: checkpoint canonical research model at cutoff"
```

---

### Task 7: 做完整语义矛盾审计与端到端回归

**Files:**
- Modify: `tests/scientist/test_context_contract.py`
- Modify: `tests/scientist/test_oneworld.py`
- Modify: `scientist/__init__.py`
- Modify: `scientist/cli.py`
- Modify: `docs/design/scientist完整上下文重构实施文档.md`（仅当实现接口与计划确有偏差时同步）

**Interfaces:**
- Produces: 最终 assembled context snapshot contract。
- Produces: 一个 scripted end-to-end 流程：grounding → Current Model → collaborator → signed report → Scientist revision → conclusion。

- [ ] **Step 1: 增加最终 prompt 全量 forbidden-pattern 审计**

```python
def test_complete_context_has_no_legacy_single_agent_semantics():
    text = _prompt().lower()
    forbidden = {
        "seat charter", "your lens is your identity",
        "your assistant", "assistant is claude code",
        "your hands", "your amplifier", "strongest executor",
        "after every work cycle", "research notebook (revisable",
        "working notes (the append-only log",
    }
    hits = sorted(item for item in forbidden if item in text)
    assert hits == []
```

- [ ] **Step 2: 增加端到端 scripted-policy 回归**

测试必须断言：

1. 第一轮 system 有 Charter、Team Constitution、无 Current Model 提示；
2. Scientist 调 `revise_current_model` 后，下一轮 system 直接出现新 model；
3. `start_investigation` 的 tool result 只含 receipt；
4. Claude 完成后以独立署名 user message 到达；
5. report 不自动修改 Current Model；
6. Scientist 第二次 `revise_current_model` 后才形成新 head；
7. compaction 后 Current Model 仍在 system 中；
8. terminal conclusion 仍通过 exit contract。

- [ ] **Step 3: 运行单 Scientist 全套测试**

Run: `python -m pytest tests/scientist -q`

Expected: PASS，且不需要网络或真实 Claude CLI。

- [ ] **Step 4: 运行全项目测试，识别意外污染到 host/evolution 的接口**

Run: `python -m pytest -q`

Expected: PASS。若旧 host tests 因 `NATIVE_TOOLS` 名称共享而失败，优先把新 channel surface 限定在顶层入世 path；不要顺手重写 host Scientist。

- [ ] **Step 5: 运行静态语义搜索**

Run:

```bash
rg -n "your hands|your amplifier|strongest executor|after every work cycle|Your assistant" scientist/agent.py scientist/native_tools.py scientist/prompts/scientist.md scientist/prompts/research_team.md scientist/research_skills
```

Expected: 对单 Scientist assembled context 可达文件无命中；若 legacy `scientist/host/` 或未加载的 `proposer.md` 有命中，记录为明确范围外，不得重新注入新 context。

- [ ] **Step 6: 用 probe 查看真实首轮 channel 选择，不把一次输出当作验收**

Run:

```bash
python -m scientist.cli --spec examples/xsbench_opt/spec.json --world <prepared-world> --probe
```

Expected: assembled context 中团队关系和 Current Model 层级正确；首轮允许 `read_file`、`bash` 小 probe、`ask_collaborator` 或 `revise_current_model`，不得因测试硬编码要求某一个固定动作。Probe 只用于人工语义检查，自动验收以 deterministic tests 为准。

- [ ] **Step 7: 更新 package/CLI 文档字符串**

删除“assistant beside it / two hands”等旧总述，改成 persistent Scientist、research team、Current Model、signed reports 与 archive 的真实关系。

- [ ] **Step 8: 运行 diff 检查与最终测试**

Run: `git diff --check`

Expected: 无 whitespace error。

Run: `python -m pytest tests/scientist -q`

Expected: PASS。

- [ ] **Step 9: 提交**

```bash
git add scientist tests/scientist docs/design/scientist完整上下文重构实施文档.md
git commit -m "feat: complete scientist context redesign"
```

---

## 3. 实施顺序为什么不能拆散

Task 1 先建立最终 assembled-context contract，是因为后续任一局部修改都可能被其他 block 否认。Task 2 先产生 canonical Current Model，Task 3 才能把 Charter 与团队关系锚在真实运行状态上。Task 4 修改 collaborator 的消息地位，Task 5/6 再解决跨轮、compaction 和 cutoff 的连续性。最后 Task 7 用完整上下文和端到端轨迹检查所有局部修改是否仍表达同一个世界观。

不能采用以下实施顺序：

```text
先改 work tool 文案
→ 再加一段 Team Constitution
→ 保留旧 cold start / notebook / nudges
→ 认为 Claude 已经成为 collaborator
```

这会产生表面新角色、深层旧 policy 的混合上下文。

---

## 4. 数据兼容与后续共享数据库边界

本轮建立独立的 SQLite `ResearchModelStore`。Standalone 默认库是 `.scientist/research_models.sqlite3`；`spec.research_model_db` 可以指向外部 run-level DB，因此未来 SimpleEvolution 可以让多个分支使用同一个物理库，而不需要再次迁移 model history。旧 `.scientist/research_state.jsonl` 只作为一次性、幂等的 legacy import 来源，不再接收新 revision。

新的 revision 现在就稳定包含：

```text
research_model_id
scientist_id / thread identity
world_id
revision
current_model
revision_reason
evidence_refs
supersedes_model_id
created_at
```

本轮所有查询都强制限定当前 `scientist_id + world_id`，只实现“自己的当前模型与历史”。以后开放跨分支查询时，自己的旧模型与其他分支模型可以使用同一物理表，但认知语义不同：自己的旧模型是 autobiography；其他 Scientist 的模型是特定 world 下的署名 memo。两者都默认 pull-only，不能自动合并进当前 Scientist 的 Current Model。

---

## 5. 完成判据

只有同时满足以下条件，本次改造才算完成：

1. 最终 assembled context 在任何位置都不把 Claude 定义为 Scientist 的 tool/hands/amplifier。
2. Research Team Constitution 与 Charter 同级并先于所有 channel schema 出现。
3. 单 Scientist 每次模型调用都直接看到唯一 Current Model；compaction 和 resume 不会丢失它。
4. notebook、notes、session trajectory 和 raw collaborator transcript 不再默认进入活跃上下文。
5. collaborator 报告作为带身份的独立消息到达，不自动触发 Current Model 更新。
6. `revise_current_model` 只表达 Scientist 自己的当前认识、修改理由和 evidence refs。
7. model history 已进入 SQLite，可查看但默认只呈现薄索引；正文必须主动 pull。
8. 同一 DB 中的不同 Scientist/world head 不互相污染；旧 `working_model` JSONL 幂等导入，旧 run 不丢失记录。
9. 单 Scientist tests 与全项目 tests 全部通过。
10. 至少一个完整 scripted trajectory 证明局部 tool、nudge、compaction、report 和 terminal 共同服从同一语义合同。
