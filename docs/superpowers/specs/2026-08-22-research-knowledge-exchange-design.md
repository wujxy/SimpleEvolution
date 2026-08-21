# Research Knowledge Exchange 设计

## 1. 设计结论

SimpleEvolution 不直接合并 branch，而是在不同 branch 之间共享实验财富。

系统采用以下认识论边界：

> 共享 world-scoped 实验事实，按需传阅带来源、可修正的研究解释，
> 研究决策仍由当前 Scientist 独立形成。

Research Knowledge Exchange 不是新的知识库或智能导师，而是建立在现有
Node、Experiment、ResearchState 和检索能力之上的信息流动机制。它不创建
第二套事实来源，不推荐研究方向，也不改变 Frontier 的选择职责。

```text
Evidence merge
    = 全组能够发现各 branch 实际发生过什么

Belief exchange
    = Scientist 可主动查阅前人的署名解释，但系统不替它融合信念

World synthesis
    = 当前 Scientist 提出普通 Proposal，由新 Experiment 验证成果能否组合
```

## 2. 目标与非目标

### 2.1 目标

- 避免不同 branch 重复支付已经发生过的实验成本；
- 让 Scientist 能发现自己原本不知道的跨 branch 证据；
- 允许 Scientist 在查看具体证据后，主动查阅其 originating ResearchState；
- 允许一个新 ResearchState 综合多个 branch 的 evidence；
- 让互补成果通过普通 Proposal 和 Experiment 形成新的 Child world；
- 保留认知多样性，避免成功方向因信息曝光而形成群体路径依赖。

### 2.2 非目标

本次不实现：

- `merge_branch` 或 patch 自动拼接；
- 独立 Knowledge 表或 Knowledge Object Store；
- 自动机制抽取、结论总结或知识蒸馏；
- supported / contradicted / confidence 等信念状态机；
- `SynthesisProposal` 等新的科研 workflow 类型；
- Knowledge Exchange 驱动的 Frontier fitness 或 research budget；
- 把 sibling ResearchState 自动注入 Scientist 启动上下文。

## 3. 三种 merge

### 3.1 Evidence merge

所有 branch 的 Experiment Ledger 构成共同的权威实验历史。

Evidence 的基本语义是：

```text
Evidence = World + Intervention + Condition + Observation
```

它表达：

> 在 parent world W 上实施 intervention X 后，在 evaluation condition C 下
> 观测到 outcome Y。

它不自动表达：

> X 一定通过 mechanism M 导致 Y。

`source world` 是一等语义，但优先由 Experiment 与 Node 的现有 identity
确定性解析，不为此重复保存一套字段。所有 evidence 查询结果和详情输出都必须
显式呈现 source node / source SHA，使旧世界事实不能被误读成当前世界事实。

### 3.2 Belief exchange

ResearchState 是某个 Scientist 在特定 world 上形成的主观 working model。
系统可以保存和传阅它，但不能将其提升为全组事实。

Sibling ResearchState 遵循二次主动访问：

1. 启动上下文不展示 sibling ResearchState；
2. 普通 search 不返回 sibling working model；
3. Scientist 先主动查看一个具体 Experiment；
4. Scientist 再通过该 Experiment 查阅 originating ResearchState；
5. 返回内容必须标记为 subjective research memo，并包含 source world、
   source episode 和 evidence refs；
6. 当前 Scientist 如受其启发，仍需注册自己的 ResearchState。

因此组织可以保存 beliefs，但只把它们作为有作者、有来源、可修正的研究备忘录：

> Evidence is authoritative. Beliefs are attributed and revisable.

### 3.3 World synthesis

不同 branch 的收益不能机械相加。两项干预可能互补、冲突，或只是消除了同一
瓶颈。因此不存在直接 World merge。

```text
Evidence A ─┐
            ├──> Current ResearchState ──> Proposal ──> Experiment
Evidence B ─┘                                      │
                                                  ▼
                                             Child Node
```

综合发生在当前 Scientist 的 ResearchState 中；组合实现发生在当前 world 的
Executor 实验中；只有通过现实评价后，组合结果才成为新的 Child Node。

不增加 `SynthesisProposal`。一个 ResearchState 可以引用多个跨 branch
evidence refs，每个 Proposal 仍然必须且只能来源于一个当前 ResearchState。

## 4. 组件与职责

### 4.1 Experiment Ledger

Experiment Ledger 是跨 branch 共享事实的唯一权威来源，保存已有的 parent、
proposal、执行结果、metrics、gate、diff/artifact 和 child identity。

Knowledge Exchange 不复制或改写 Ledger 内容，只提供受约束的查询视图。

### 4.2 Shared Evidence View

Shared Evidence View 是 Experiment Ledger 的全局、跨 branch 投影，负责：

- evidence 搜索；
- coverage 汇总；
- source-world 显示；
- 具体 Experiment 的按需检查；
- 相关、对照和多样结果的可发现性。

它不输出机制结论、下一步建议或“promising”之类价值判断。

### 4.3 Research Memo View

Research Memo View 通过已有 identity 链确定性解析：

```text
Experiment
    -> originating Proposal
    -> originating ResearchState
    -> source Episode / Node / evidence refs
```

它不是新领域对象或总结器，只是对已有 ResearchState 的带 provenance 只读视图。

### 4.4 Finding

Finding 继续是 research bookkeeping：研究问题、mechanism/code-region 标签、
operational state、coverage 与关联 experiment refs。

Finding 不是 mechanism discovery，也不保存 LLM 结论。它不能表达“某方法最好”
或“某机制已被验证”。

### 4.5 Current Scientist

当前 Scientist：

- 根据当前 world 独立形成 ResearchState；
- 主动搜索跨 branch evidence；
- 在查看具体 Experiment 后决定是否读取其 Research Memo；
- 可以接受、修改或拒绝前人的解释；
- 必要时调用 Cognitive Transformation；
- 从一个当前 ResearchState 提交 Proposal。

### 4.6 Frontier

Frontier 仍然只选择客观 Node，并分配 world 的生存和发展预算。

Knowledge Exchange 不参与 Frontier fitness，不根据 objective improvement、
流行度或引用次数提高某个方向的曝光。

## 5. 信息访问

### 5.1 启动时轻量 push

启动上下文只提供低语义 coverage 和近期权威 outcome，例如：

- explored code regions；
- experiment counts；
- 由 gate / eligible / selected 等机械字段得到的 outcome distribution；
- recent objective observations；
- experiment ids 与 source worlds。

启动 push 不包含：

- sibling ResearchState；
- Proposal instruction 或 rationale；
- LLM 生成的机制摘要；
- “最佳”“值得继续”“promising”等方向判断；
- 按 objective gain、流行度或引用次数形成的推荐排名。

### 5.2 第一层 pull：Evidence

Scientist 使用全局查询发现已经覆盖的研究区域，再主动检查具体 Experiment。

概念接口：

```text
search_experiments(query, filters, limit, buckets)
inspect_experiment(experiment_id)
```

Search 结果是 coverage/evidence 索引，不返回 Proposal 全文或 ResearchState。
`inspect_experiment` 返回该 Experiment 的具体 world、intervention、condition、
observation 和 artifact refs。

### 5.3 第二层 pull：Research Memo

Scientist 查看具体 Experiment 后，可以继续请求：

```text
inspect_originating_research_state(experiment_id)
```

运行时只允许访问本 Episode 已检查过的 Experiment。返回值包含：

- originating research state id；
- working model；
- source node / SHA；
- source episode；
- evidence refs；
- 明确的 `SUBJECTIVE_RESEARCH_MEMO` 标记。

如果 Experiment 没有关联 ResearchState，返回结构化的 unavailable 结果；系统
不能为旧实验补写或推断一个 ResearchState。

## 6. 信息暴露与选择压力

Knowledge Exchange 不可能完全没有影响：push 内容、search 排序和结果截断都会
改变 Scientist 接触信息的概率。

因此边界不是“没有 selection pressure”，而是：

> Knowledge Exchange 不拥有 world survival selection，也不以 objective success
> 推荐方向；其信息暴露策略必须明确、可审计，并服务于 relevance、coverage
> 和 diversity。

允许的检索信号：

- 与 Scientist 主动 query 的相关性；
- 已探索区域的 coverage；
- 对照 outcome；
- 不同 branch / region 的多样性；
- 必要的 recency 与 world compatibility。

禁止的推荐信号：

- objective gain 越高曝光越多；
- 被引用越多曝光越多；
- 已有成功 branch 获得默认优先级；
- LLM 评价某方向“更重要”。

## 7. Transformation 的关系

Cognitive Transformation 是可选认知变异，不是固定 pipeline stage：

```text
Evidence / Memo
       |
       v
Current ResearchState --------------------> Proposal
       |
       +--> Transformation Challenge
                    |
                    v
             Revised ResearchState ------> Proposal
```

Knowledge Exchange 提供外部材料；Transformation 挑战当前 framing；两者都不能
替 Scientist 注册 ResearchState 或提交 Proposal。

## 8. 失败与边界处理

- 未知 Experiment id：返回明确的 not-found error；
- 未先 inspect Experiment 就请求其 Research Memo：拒绝访问并说明二次访问要求；
- Experiment 无 originating ResearchState：返回 unavailable，不生成推断；
- provenance identity 断裂：作为数据一致性错误暴露，不回退到模糊文本搜索；
- source world 与当前 world 不同：正常返回，但必须显式显示差异，不能自动迁移；
- 搜索无结果：返回空 coverage，不推荐替代方向；
- Research Memo 内容与 Evidence 冲突：同时原样呈现 provenance，不由 Harness 裁决。

## 9. 测试与成功标准

### 9.1 行为测试

- Scientist 启动上下文不含 sibling working model；
- Evidence 搜索覆盖所有可见 branch，而非只覆盖当前 lineage；
- search 结果不泄露 Proposal 全文或 ResearchState；
- inspect Experiment 显示 source world、conditions、metrics 与 gate；
- 未检查 Experiment 时不能读取其 originating ResearchState；
- Research Memo 始终带 subjective 标记和 source world；
- 缺失 ResearchState 时不生成总结；
- 当前 ResearchState 可以引用多个 branch 的 evidence；
- 每个 Proposal 仍只引用一个当前 ResearchState；
- synthesis 仍走普通 Proposal -> Experiment -> Child Node；
- Frontier 的 scoring 和 budget 行为不因 Knowledge Exchange 改变。

### 9.2 运行观测

MVP 只记录、不用于选择：

- 跨 branch evidence 查询次数；
- Research Memo 二次查看次数；
- 新 ResearchState 引用跨 branch evidence 的比例；
- 引用多个 branch evidence 的 Proposal 数量；
- 重复 intervention 的比例；
- 不同 ResearchState / branch 的方向分布是否发生塌缩。

这些指标用于判断 Knowledge Exchange 是否减少重复、促进综合而没有造成群体
收敛；它们不进入 Frontier fitness。

## 10. 设计不变量

1. Experiment Ledger 是跨 branch 实验事实的唯一权威来源。
2. Evidence 必须绑定 source world、intervention、condition 和 observation。
3. Mechanism 和意义解释属于 Scientist judgment，不属于 Harness fact。
4. Sibling ResearchState 默认不可见，只能沿已检查 Experiment 二次主动访问。
5. Research Memo 必须带作者来源、source world 和主观标记。
6. 当前 Scientist 必须注册自己的 ResearchState，不能直接采用 sibling state。
7. 一个 ResearchState 可以引用多个跨 branch evidence refs。
8. 每个 Proposal 必须且只能来源于一个当前 ResearchState。
9. 不存在 branch merge operation；组合成果必须通过新 Experiment 验证。
10. Finding 仍是 research bookkeeping，不是结论数据库。
11. Knowledge Exchange 不改变 Frontier scoring 或 research budget。
12. Knowledge Exchange 不以 objective success、流行度或引用次数推荐方向。
13. Cognitive Transformation 是可选认知回路，不是强制 pipeline stage。
14. 不新增现有 Harness 无法机械处理或真实运行尚未证明必要的领域对象。

## 11. 最终原则

> SimpleEvolution 不合并 branch，而是共享各 branch 的实验财富。研究组织以
> evidence 作为共同权威，以署名、可修正的 Research Memo 保存解释；当前
> Scientist 独立形成 ResearchState，并通过新的 Experiment 把可能兼容的财富
> 转化为一个真实的新 world。
