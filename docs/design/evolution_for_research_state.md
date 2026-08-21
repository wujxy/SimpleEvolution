# Research State Evolution 设计

## 1. 设计结论

SimpleEvolution 维护一棵客观的 Node Tree，但由 Proposer 形成、由
Proposal 携带、并在代际间演化的认知对象是 `ResearchState`。

本次设计只引入两个认知核心：

```text
ResearchState
    = Scientist 对课题的当前认识，也是认知演化对象

CognitiveTransformation
    = Scientist 向外部“导师”陈述当前认识后，由一个生成元引导的认知挑战
```

Evidence 不是第三个演化核心。现有 `evidence_refs` 只是 ResearchState 对
源码、实验和 finding 等已有材料的可选来源引用。真正的
Chain-of-Evidence / Evidence Compiler 将声明与原始材料、执行记录和产出绑定，
并验证其可追溯性与一致性；这是后续独立设计，不属于本次 MVP。

```text
Node
  = 项目在一个确定 source SHA 上的客观研究进度

ResearchState
  = Scientist 对当前 Node 的一个可修正 working model

Proposal
  = 从一个 ResearchState 导出的、值得交给现实检验的实验方向
```

系统不增加第二棵调度树。Scheduler 仍然通过 Frontier 给 Node 分配研究
预算；ResearchState 作为 Node 内部的认知演化对象，记录在 Episode、
Proposal 和 Child handoff 之间。

完整关系为：

```text
Harness
  │
  │ Frontier 给 Node 分配发展预算
  ▼
Proposer Episode @ Node
  │
  ├── inspect evidence
  ├── transform_worldview(...)
  ├── register_research_state(...)
  └── submit_proposal(research_state_id=...)
             │
             ▼
         Experiment
             │
             ▼
         Child Node
             │
             ▼
  originating ResearchState
  + proposal expectation
  + experiment outcome
  + child world transition
             │
             ▼
  Child Proposer 形成修正后的 ResearchState
```

这项设计的主要目的不是增加更多 Proposal，而是让系统能够回答：

- Scientist 当时如何理解问题；
- 哪些证据促成了该理解；
- Proposal 从哪个 working model 导出；
- 实验结果修正了哪个认知起点；
- 一个认知方向在后续 Node 中如何延续、分化或被放弃。

---

## 2. 为什么演化对象需要是 ResearchState

LLM Scientist 通常不缺少局部优化能力，真正的限制是容易停留在单一
problem framing 中。

例如：

```text
Observation:
    FCN dominates OMILREC runtime

Initial working model:
    FCN implementation is inefficient

Derived proposals:
    cache
    SIMD
    memory layout
    parallelization
```

这些 Proposal 在实现层面不同，但共享同一个关键前提：

```text
FCN 必须以当前形式存在，问题只是它执行得不够快。
```

另一种 ResearchState 可能是：

```text
重复 FCN evaluation 不是局部实现问题，而是 optimizer、FCN 和物理状态
之间的边界切断了可复用状态的生命周期。
```

它会导出完全不同的实验：

- 测量相邻 evaluation 间的不变量；
- 减少 FCN 调用次数，而不是优化单次调用；
- 改变状态所有权和模块边界；
- 重新表达 reconstruction objective。

因此树宽不能用 Proposal 数量定义。三个共享同一 working model 的 Proposal
仍然是一个认知方向。SimpleEvolution 需要保存和演化产生 Proposal 的
ResearchState，才能区分实现变化与问题理解变化。

---

## 3. 单树模型与职责边界

### 3.1 Node：客观 Research World

Node 继续保持当前语义：

- exact source SHA；
- parent Node；
- producing Experiment；
- metrics；
- gate result；
- tree depth；
- lifecycle status。

Node 是 Harness 持有的事实对象，不保存 Scientist 对世界的主观解释。

### 3.2 Episode：一次完整研究行为

Episode 表示一个 Proposer/Scientist 被分配到某个 Node 后完成的一次研究：

- 查看当前世界；
- 调取证据；
- 形成零个或多个 ResearchState；
- 提交零个或多个 Proposal；
- 冻结本次研究轨迹后终结。

Episode 是 single-use 的认知行为 identity，不是长期进程，也不是
ResearchState 本身。

### 3.3 ResearchState：可演化的 working model

ResearchState 是 Scientist 对当前 Node 的一个不可变认知快照。它的核心
内容使用自由文本 `working_model`，而不是强制 Scientist 填写固定的
assumptions、causal model、open questions 等表格。

一个 working model 可以自然包含：

- 当前对问题的解释；
- 解释依赖的关键假设；
- 已知不确定性；
- 值得向现实询问的问题；
- 可能的机制和干预方向。

示例：

```text
当前我认为主要问题不是单次 FCN 执行效率，而是状态生命周期被
optimizer/FCN 的边界切断，导致本可跨调用复用的信息反复生成。

这个理解依赖于相邻调用间存在足够多的不变量。目前还不能确定这些
不变量的计算成本是否足以解释 total_ms。

接下来值得询问现实的问题是：跨调用保存这些状态后，FCN call count、
FCN local time 和 total time 是否一起变化，还是只移动局部指标。
```

ResearchState 是 Scientist judgment，不是 Harness 事实。它可以大胆、
不完整甚至错误，但必须能够追溯到形成它时可见的证据和认知来源。

### 3.4 Proposal：一个 ResearchState 的实验表达

每个 Proposal 必须且只能引用一个 ResearchState：

```text
Proposal N ──> exactly 1 ResearchState
```

一个 ResearchState 可以产生零个或多个 Proposal：

```text
ResearchState 1 ──> 0..N Proposal
```

不采用“一个 ResearchState 最多一个 Proposal”的硬约束。一个完整的
working model 可能自然导出多个有区别的诊断或干预实验。强制一对一会
诱导 Scientist 复制多个近似 ResearchState，制造虚假的认知宽度。

“优先让不同 ResearchState 各产生一个 Proposal”只作为软提示。同一
ResearchState 下的后续 Proposal 使用现有 `material_difference` 说明它检验
的不同不确定性、干预机制或 expectation。

### 3.5 Experiment：现实检验

Experiment 保持客观事实语义：

```text
Parent Node → Proposal → Experiment → Child Node
```

Experiment 不直接宣布 ResearchState 被证明或推翻，只记录实际执行的
intervention、metrics、gate、diff 和结果 SHA。

---

## 4. ResearchState 最小数据模型

ResearchState 只结构化 Harness 必须机械处理的 provenance，不结构化
Scientist 的思考方式。

```text
ResearchState
    research_state_id
    node_id
    episode_id
    derived_from_research_state_id  nullable
    transformation_id              nullable
    working_model                  free-form text
    evidence_refs                  list[EvidenceRef]
    created_at
```

字段语义：

- `research_state_id`：不可变 identity；
- `node_id`：该认知解释的客观世界；
- `episode_id`：形成该认知的研究行为；
- `derived_from_research_state_id`：认知来源，不代表来源正确；
- `transformation_id`：促成本次变异的 Cognitive Transformation；
- `working_model`：Scientist 自由表达的当前理解；
- `evidence_refs`：形成该理解时主动绑定的证据引用；
- `created_at`：注册时间。

`node_id`、`episode_id` 和 `created_at` 由运行时自动填写。Scientist 不应
被要求重复提供 Harness 已知的 identity。

ResearchState 一经注册不原地修改。Scientist 改变看法时注册一个新的
ResearchState，并通过 `derived_from_research_state_id` 建立认知演化关系。

Cognitive Transformation 另有一条最小 provenance 记录：

```text
CognitiveTransformation
    transformation_id
    node_id
    episode_id
    source_research_state_id  nullable
    operator_id
    challenge
    created_at
```

它只记录某次变异调用的输入、使用的 generator 和返回的认知挑战，不表示
Scientist 接受了该挑战。只有后续 ResearchState 显式引用
`transformation_id`，才能说明该 working model 是在这次变异影响下形成的。

当前 MVP 不增加这些强制字段：

- assumptions；
- root cause；
- causal model；
- open questions；
- hypothesis family；
- intervention family；
- supported / contradicted / validated。

它们可以自然写在 `working_model` 中。只有真实运行证明 Scheduler、
ResearchStateSeedBuilder 或 reporting 必须机械查询某项语义时，才将该项
提升为结构化字段。

---

## 5. Cognitive Transformation 与变异因子

### 5.1 定义

Cognitive Transformation 是作用于 Scientist 当前 problem framing 的认知
变异算子：

```text
Current ResearchState
        │
        │ cognitive transformation
        ▼
Transformation Challenge
        │
        │ Scientist judgment
        ▼
Alternative ResearchState
```

它不是 Proposal generator，不直接给出 solution，也不替 Scientist 注册
新的 working model。

### 5.2 Generator basis

现有 generator basis 可以继续作为 Cognitive Transformation 的有限变异
算子基，例如：

- Assumption Attack；
- Boundary Shift；
- Causal Inversion；
- Abstraction Shift；
- Representation Change；
- Cross-domain Analogy；
- Counterfactual Design；
- Limit / Scale Shift；
- Anomaly Amplification；
- Cost Structure Attack。

Generator 应改变问题表示，而不是预先指定 cache、parallel、refactor 等
具体 solution family。

### 5.3 `transform_worldview`

```text
transform_worldview(
    source_research_state_id?,
    operator?
) -> {
    transformation_id,
    operator,
    challenge
}
```

`challenge` 可以包含：

- 当前 framing 中值得攻击的假设；
- 另一种可能解释；
- 反事实或边界变化；
- 新的研究问题。

返回值不是 ResearchState。Scientist 可以忽略、修改、组合或递归应用变异
结果，最后通过 `register_research_state` 表达自己的判断。

Generator 可以由 Scientist 主动选择，也可以在需要扩大认知宽度时由
Harness 提供 variation factor。无论来源如何，Harness 不判断变异结果是否
科学正确。

---

## 6. Scientist 研究工具

### 6.1 `register_research_state`

```text
register_research_state(
    working_model,
    evidence_refs=[],
    derived_from_research_state_id=null,
    transformation_id=null
) -> research_state_id
```

机械校验：

- `working_model` 非空；
- parent ResearchState 存在且对当前 Episode 可见；
- transformation id 来自本 Episode 可见的变异调用；
- evidence refs 可解析且对当前 Scientist 可见；
- `node_id` 和 `episode_id` 由 Runtime 绑定，不能由模型伪造。

该工具只注册 Scientist 的判断，不将其中任何陈述提升为事实。

### 6.2 `submit_proposal`

```text
submit_proposal(
    research_state_id,
    instruction,
    expectation,
    material_difference=null
) -> proposal_id
```

机械校验：

- ResearchState 已注册；
- ResearchState 属于当前 Node 和当前 Episode；
- Proposal 有清晰的 executor instruction；
- expectation 可以与返回结果对照；
- 同一 ResearchState 下已有近似 Proposal 时，要求说明
  `material_difference`。

ResearchState 与 Proposal 不要求一对一。是否提交、提交多少，由 Scientist
基于证据和实验成本自主决定。

### 6.3 自主性原则

三个工具是认知 provenance 协议，不是固定 workflow。系统不要求：

```text
Step 1 transform
Step 2 register
Step 3 submit
```

Scientist 可以先调查、注册状态后继续修改理解、直接注册原创状态、调用多次
transform、或者最终 abstain。唯一硬边界是 Proposal 必须显式说明自己来自
哪个已注册 ResearchState。

---

## 7. Child Node 的认知继承

### 7.1 继承原则

Child 不直接继承父 Episode 的完整 session 作为热认知上下文。一个 Episode
可能形成多个 ResearchState 和 sibling Proposal；复制完整 session 会把其它
分支的 working model 和 Proposal 一起带入 Child，破坏分支归因。

Child 的认知起点是：

```text
originating ResearchState
+ Proposal instruction and expectation
+ Experiment outcome
+ Child World transition
```

但 originating ResearchState 不能原样被宣布为 Child 的当前真相。实验可能
支持、削弱或重新解释其中不同部分。

### 7.2 ResearchStateSeed

Harness 通过 ResearchStateSeedBuilder 为 Child 生成确定性的启动材料：

```text
ResearchStateSeed
    child_node facts
    originating_research_state_id
    originating working_model
    proposal_id
    proposal instruction
    preregistered expectation
    experiment metrics
    gate result
    changed paths
    parent metrics
    referenced evidence
```

ResearchStateSeed 不是一个已注册 ResearchState，也不包含自动生成的科学
结论。Child Proposer 必须重新检查当前世界，并根据启动材料注册新的
ResearchState。

MVP 中 ResearchStateSeed 由一个确定性的 builder 按需组装，不需要独立的
持久化表或新智能组件；其全部输入都来自已有的 Node、ResearchState、
Proposal 和 Experiment 记录。

因此：

```text
Harness 组装事实与认知来源；
Scientist 形成修正后的理解。
```

L1 仍保存父 Episode 的完整原始轨迹，供追溯和显式查询，但不会无条件作为
Child 的热上下文。

---

## 8. ResearchStateSeedBuilder 与未来 Chain-of-Evidence

### 8.1 ResearchStateSeedBuilder 的定位

ResearchStateSeedBuilder 是 Child 上下文的确定性组装函数，不是新的领域对象、
LLM 总结器、Reviewer 或 epistemic judge。

它只沿已有 identity / foreign-key 关系读取：

```text
Child Node
    → producing Experiment
    → originating Proposal
    → originating ResearchState
```

它负责回答：

- 某个 Proposal 来源于哪个 ResearchState；
- Proposal 在实验前登记了什么 expectation；
- 实验实际上改变了什么、测得什么；
- Child Proposer 应看到哪些对应材料。

它可以原样携带 ResearchState 已保存的 `evidence_refs`，但不解析、搜索或验证
这些引用。它的实现应当是一个小型纯组装边界，而不是独立 Evidence 子系统。

### 8.2 未来 Chain-of-Evidence / Evidence Compiler 的定位

真正的 Evidence Compiler 面向科研可信性，而不是 Child handoff。它连接：

```text
research claim
    → declared evidence
    → source / code / experiment config / execution log / raw metrics
    → verification and auditable evidence bundle
```

它负责检查声明是否有来源、数值是否来自对应实验、方法描述是否符合代码、
产出是否能够被复现或审计。该能力可以利用本次保留的 `evidence_refs`、
ResearchState provenance 和 Experiment Ledger，但需要独立的 claim model、
验证规则与产出格式，因此明确推迟到后续设计。

本次 Research State 演化不实现声明抽取、claim-to-evidence 验证、引用真实性
检查、方法—代码一致性检查或 proof pack。

### 8.3 不负责的科学判断

ResearchStateSeedBuilder 不输出：

- “该假设已被证明”；
- “该 worldview 已被推翻”；
- “该方向值得继续”；
- “该 ResearchState 优于另一个 ResearchState”。

这些都是 Scientist judgment 或现实评价，不是确定性 builder 的职责。

### 8.4 本次保留的身份链

```text
ResearchState RS1 + optional evidence_refs
    │ research_state_id
    ▼
Proposal P1 + expectation
    │
    ▼
Experiment X1 + objective outcome
    │
    ▼
ResearchStateSeed @ Child Node
    │ Scientist revision
    ▼
ResearchState RS2
```

该身份链为后续 Chain-of-Evidence、reporting 和研究分析提供 provenance，但
本次只要求它能够区分：

- 事实本身；
- Scientist 当时对事实的解释；
- 从解释导出的实验；
- 实验对后续理解提供的现实修正材料。

---

## 9. 树宽与预算

### 9.1 Harness 的宏观职责

Harness 通过 Frontier 决定哪些 Node 获得 Proposer capacity。它维护的是客观
Research Tree 的发展预算，不判断 ResearchState 的语义质量。

### 9.2 Proposer 的微观职责

Proposer 在一个 Node 内：

- 形成自己的 working model；
- 必要时调用 Cognitive Transformation；
- 注册一个或多个 ResearchState；
- 从值得实验的 ResearchState 提交 Proposal；
- 对没有足够证据或价值的方向 abstain。

树宽来自两层共同作用：

```text
macro width
    = Frontier 中获得发展预算的不同 Node

cognitive width
    = 一个 Node 上产生有效 Proposal 的不同 ResearchState
```

### 9.3 Node 生命周期 Proposal 预算

`proposal_slots` 不能承担 Node 生命周期预算。它最多表示一次 Episode 能提交
多少 Proposal，而 Node 经多次 reseed 后会重复获得 slots。

新增概念：

```text
max_proposals_per_node
    = 一个 Node 整个生命周期最多发布的 Proposal 数
```

每次 allocation 的可用额度：

```text
remaining =
    max_proposals_per_node
    - published proposals
    - concurrent reservations

allocation_cap =
    min(max_proposals_per_episode, remaining)
```

Proposer 自主提交 `0..allocation_cap` 个 Proposal。额度是 ceiling，不是 quota。
并发 allocation 必须由 Scheduler 原子预留 Proposal identity；未使用的 reservation
在 Episode 结束后释放。

保留两个互相独立的预算：

- `max_research_per_node`：Node 最多获得多少次 Proposer allocation；
- `max_proposals_per_node`：Node 最多发布多少个实验方向。

前者控制 Proposer 计算资源，后者控制从该 Node 派生的实验宽度。只保留后者
会允许连续 abstain 的 Node 无限消耗研究计算预算。

### 9.4 宽度遥测

MVP 先观察，不将认知指标直接加入 Frontier fitness：

- 每个 Node 获得的 Episode 数；
- 每个 Episode 注册的 ResearchState 数；
- 有 Proposal 的不同 ResearchState 数；
- Proposal 在 ResearchState 间的集中度；
- transformation operator 使用分布；
- 单个 ResearchState lineage 消耗的实验预算；
- Child 中认知被延续、修正或放弃的显式谱系。

Harness 不通过文本相似度、embedding 或 LLM judge 裁决两个 working model 是否
“真的不同”。如果运行数据证明表面多样性仍然严重，再单独设计语义多样性
评价；当前不提前增加这层复杂度。

---

## 10. 与当前实现的关系

当前 SimpleEvolution 已经具有该设计的大部分雏形：

- `Node` 已表示 source SHA、metrics、gate 和 parent relationship；
- `Episode` 已记录 Node 上的一次 Scientist research act；
- `session.jsonl` 和 `notebook.md` 已保存隐式认知状态；
- `inherited_from_episode_id` 已建立认知连续性；
- `variation_operator` 和 `generator.json` 已提供变异因子；
- Proposal 已有 `rationale`、`evidence_refs` 和 `material_difference`；
- Experiment Ledger 已保存客观结果；
- `world_transition` 已向 Child Scientist 传递现实变化；
- Frontier 已负责 Node 级 research budget allocation。

需要统合的缺口是：

1. ResearchState 目前隐藏在完整 session/notebook 中，没有独立 identity；
2. Proposal 没有显式引用产生自己的 working model；
3. generator 目前主要作为 reseed hint，变异结果没有 provenance；
4. Child 复制完整父 session，不能隔离 originating ResearchState；
5. Child 上下文缺少按 originating ResearchState 精确组装的 seed builder；
6. Proposal budget 是 per-allocation slots，不是 per-Node lifetime budget。

该设计不是重建 Proposer 或 Scheduler，而是把这些已有机制围绕一个明确的
ResearchState 演化对象重新连接。

---

## 11. MVP 范围

### 必须实现

- ResearchState identity 和最小持久化模型；
- `register_research_state`；
- `transform_worldview` 及 transformation provenance；
- `submit_proposal(research_state_id=...)`；
- Proposal N:1 ResearchState 关系；
- Child ResearchStateSeed；
- 确定性的 ResearchStateSeedBuilder；
- `max_proposals_per_node` 与并发 reservation；
- 相关 schema、工具、继承和预算测试。

### 暂不实现

- ResearchState semantic similarity judge；
- embedding-based novelty；
- LLM 评价 worldview 优劣；
- assumptions / causal model 的强制结构化 schema；
- ResearchState 独立 Frontier；
- 第二棵 cognitive tree scheduler；
- 自动宣布 supported / contradicted / validated；
- 新的 Reviewer agent；
- 为完整性增加独立 Hypothesis 对象；
- Chain-of-Evidence / Evidence Compiler 的 claim-to-evidence 验证与 proof pack。

这些能力只有在真实运行暴露明确 failure mode 后再讨论。

---

## 12. 设计不变量

1. Node 只保存客观世界，不保存主观 worldview。
2. ResearchState 是 Scientist judgment，不是 Harness fact。
3. ResearchState 注册后不可原地修改。
4. 每个 Proposal 必须且只能引用一个 ResearchState。
5. 一个 ResearchState 可以产生零个或多个 Proposal。
6. ResearchState 与 Proposal 的一对一只可作为软提示。
7. Cognitive Transformation 不直接产生 Proposal 或注册结论。
8. ResearchStateSeedBuilder 只沿 identity 关系组装 Child 材料，不解析或验证
   evidence。
9. Child 继承 originating ResearchState 和对应实验结果，不热继承 sibling
   ResearchState。
10. Frontier 仍选择 Node；ResearchState 不引入第二棵调度树。
11. Proposal budget 是 ceiling，不是要求 Scientist 填满的 quota。
12. 只结构化 Harness 当前必须机械处理的信息。

---

## 13. 成功标准

该设计实现后，系统应能确定性回答：

- 一个 Proposal 来自哪个 ResearchState；
- Scientist 注册该 ResearchState 时看过哪些证据；
- ResearchState 是否由某个 transformation operator 变异而来；
- 一个 Experiment 检验了哪个 working model 下的哪个 Proposal；
- Child Proposer 收到了哪个 originating ResearchState、什么 expectation 和
  什么客观 outcome；
- 同一 Node 的 Proposal 数量和实际认知宽度分别是多少；
- Node 是否达到生命周期 Proposal 预算；
- 整条研究路径中，事实、认知解释和实验行动如何相互关联。

同时，Scientist 仍能以自由文本表达自己的 working model，不需要按固定
assumptions/root-cause/hypothesis 表格执行预设 workflow。
