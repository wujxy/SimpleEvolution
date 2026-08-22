# Supervisor、Integrator 与统一 Agent Runtime 设计

## 1. 设计结论

SimpleEvolution 增加两个组织级研究角色，并把现有 Scientist 的通用对话循环
抽取为可复用 Agent Runtime：

- **Supervisor** 掌握整个课题组的客观实验版图，独占正常路径的研究资源准入，
  减少重复投入、保护真实差异，并判断何时启动阶段性统合；
- **Scientist** 继续独立研究一个 Node，自主选择 `EXPLORE` 或 `SYNTHESIZE`；
- **Integrator** 是由 Supervisor 临时拉起的一次性、synthesis-only Scientist，广泛
  调查被选中的阶段成果，只提交一个 integration proposal 或明确 abstain；
- **Executor** 继续使用现有 `claude -p` 代码执行路径，不进入 Agent Runtime
  抽象；
- **Scheduler** 仍是唯一持久化写入者和机械编排者，不承担语义科研判断。

最终组织结构是：

```text
                          objective L2 facts
                                  |
                                  v
                            Supervisor
                         /                \
              resource decisions     integration request
                    |                       |
                    v                       v
                Scheduler               Integrator
                    |                       |
                    v                       v
                Scientist          integration proposal
                    |                       |
              EXPLORE / SYNTHESIZE          |
                    \                       /
                     v                     v
                           Executor
                              |
                        Eval + Gate
                              |
                     Experiment / Child Node
```

核心原则是：

> Scientist 决定研究什么，Supervisor 决定有限研究资源投向哪些 world，
> Integrator 负责把成熟分支写成一个可验证的综合方案，Harness 负责现实裁决。

## 2. 设计动机

Research Knowledge Exchange 已经让每个 Scientist 能发现跨 branch 的实验事实和
署名研究解释。运行观测随后暴露出三个结构性问题：

1. **已验证收益是私人红利**：只有主动移植成果的 lineage 获得该收益；
2. **绝对指标被基线污染**：同一个新 idea 在不同基础 world 上无法公平比较；
3. **Frontier 会饿死探索臂**：低基数但机制独特的方向可能被高基数组合节点挤出。

同时，Scientist 已经能够独立形成包含多个研究方向的 worldview，但在存在已验证
赢家时，往往只提交最安全的一个移植 proposal。仅修改 prompt 不能解决资源选择和
公共基线沉淀问题。

这正好满足原主设计 Parking Lot 中两个机制的复活条件：

- 全局语义资源管理者；
- branch synthesis / crossover。

因此本设计不是预防假想风险，而是修复已观察到的搜索坍缩。

## 3. 目标与非目标

### 3.1 目标

- 让 Scientist 明确、自主地选择探索或综合，而不是把二者混进一个 proposal；
- 让多个值得尝试的独立探索方向能够真正占用多个 proposal slot；
- 用全局语义判断完全替代 measured-axis Frontier 的正常资源准入职责；
- 让相似 Node 竞争同一研究问题时只保留必要的代表继续消耗预算；
- 让低绝对分数但机制独特、信息价值高的 Node 仍能获得研究资源；
- 在多条 branch 均形成阶段性成果后，由独立 Integrator 尝试形成共同基线；
- 用逻辑 epoch root 表达阶段性树干，同时保留完整历史和旧 branch 复活能力；
- 让 Supervisor、Scientist、Integrator 复用一个最小 Agent Runtime；
- 保持异步 job、持久化、重试、恢复和审计语义。

### 3.2 非目标

本次不实现：

- Git 意义上的自动 branch merge；
- Node 的真正多父结构；
- 自动 patch 池或自动兼容性证明；
- 多 agent 组会对话、投票、辩论或审稿流程；
- Supervisor 直接生成普通研究 proposal；
- Integrator 直接修改代码；
- Supervisor 读取 Scientist 私有 L3 会话；
- 让 Executor 改用统一 Agent Runtime；
- 为不同角色复制完整对话循环、工具分派和 session 实现；
- 用 embedding、聚类模型或新的推荐服务判断 Node 相似度；
- 复杂 bandit、强化学习或自动调参式资源策略。

## 4. 认识论和权限边界

Research Knowledge Exchange 的核心边界继续成立：

> 认知不共享，现实共享。

系统中的信息分为：

```text
Harness facts
    Node / SHA / intervention / condition / metrics / gate / changed paths

Attributed judgments
    ResearchState / Proposal rationale / Supervisor rationale /
    Integrator compatibility analysis

Private cognition
    Scientist session / notebook / unreleased trajectory
```

- Harness facts 是共同权威；
- 所有 LLM 判断都有明确作者和 world 来源，且可被后续实验推翻；
- 影响资源分配、统合或 epoch 晋升的组织级判断必须引用具体 evidence；
- Supervisor 可以读取全组公开 ResearchState，但必须把它们当作署名判断；
- Supervisor 和 Integrator 都不能读取 Scientist 私有 L3；
- Supervisor 的资源决定不会写入 Scientist ResearchState；
- Integrator 形成自己的 ResearchState，不能直接冒用任一 donor 的 worldview。

Supervisor 接收的是客观事实，但资源分配本身仍是判断。系统必须记录判断依据，
不能把“两个 Node 很接近”伪装成 Harness 事实。

## 5. 角色职责

### 5.1 Scientist

Scientist 仍然是局部研究者：

- 绑定一个当前 Node；
- 研究该 world 的代码和实验行为；
- 按需查询跨 branch evidence 和 attributed memo；
- 注册自己的 ResearchState；
- 自主选择 `EXPLORE`、`SYNTHESIZE` 或 abstain；
- 沿 Parent -> Child 保持 cognition lineage。

Supervisor 只决定 Node 是否获得一次 Scientist allocation，不决定该 Scientist
应该选择哪种研究操作。

### 5.2 Supervisor

Supervisor 是课题组级资源管理者，不要求比 Scientist 更懂任何具体课题。它负责：

- 查看当前 epoch 和历史 Archive 的客观研究版图；
- 在 Scheduler 需要新的 proposer allocation 时选择 Node；
- 判断多个 Node 是否在重复支付近似研究成本；
- 在近似 Node 中选择代表，其余 Node 暂停获得资源但不删除；
- 为真实不同、尚未成熟的方向保留实验机会；
- 发现先前暂停 Node 获得新相关证据时重新激活；
- 判断是否具备阶段性统合条件；
- 创建 integration request；
- 在 integration experiment 完成后决定是否晋升新 epoch。

Supervisor 不得：

- 替 Scientist 形成或修改 ResearchState；
- 要求普通 Scientist 必须选择 `EXPLORE` 或 `SYNTHESIZE`；
- 提交普通 Proposal；
- 修改代码或运行 Executor；
- 绕过 correctness Gate；
- 把暂停资源解释为科学结论或永久淘汰。

### 5.3 Integrator

Integrator 是由 Supervisor 临时拉起的一次性、synthesis-only“主笔 Scientist”。
它复用 Scientist 的研究循环，但不是长期存在的第四种研究身份，也不拥有独立的长期
memory、lineage 或工具体系。它负责：

- 以一个 target Node 作为唯一实现基线；
- 调查 Supervisor 指定的 donor Experiments 和相关 branch；
- 读取这些世界的源码快照、diff、metrics、gate、公开 ResearchState 和失败记录；
- 运行受控的只读研究命令和 probe；
- 判断成果互补、重叠、冲突或依赖；
- 注册一个属于自己的 integration ResearchState；
- 只提交一个 integration proposal，或明确说明暂不可统合。

Integrator 默认每次全新启动，不继承任一 donor 的私有 session，从而降低分支立场
偏见。它只能选择 `submit_synthesis` 或 abstain，不能在 integration request 中改做
EXPLORE。它可以广泛调查，但不能直接写目标源码，也不能宣布 epoch 晋升。

### 5.4 Executor

Executor 保持现有职责和 `claude -p` 运行路径：

- 接收一个普通或 integration proposal；
- 在现有 mount 和 editable-path 边界内实现修改；
- 运行 Eval 和 Gate；
- 产出 Experiment、result SHA 和可选 Child Node。

Executor 不进入本设计的 Agent Runtime 抽象。它只与其他角色共享 job envelope、
attempt、trace 和 Scheduler 编排基础设施。

### 5.5 Scheduler

Scheduler 继续承担纯机械职责：

- 唯一写入 SQLite；
- 构建 Supervisor 的全局事实快照；
- 持久化并执行 Supervisor decision；
- 创建 Scientist、Integrator 和 Experiment job；
- 校验 Node、proposal id、donor、Gate 和预算约束；
- 处理 attempt 重试、丢失恢复和 stale result；
- 在 Supervisor 不可用或输出非法时使用旧 Frontier fallback；
- 让合法 Scientist lease 或 integration request 发布的 queued Proposal 进入有界 FIFO
  Executor queue，不再使用 Frontier 对它们进行二次否决；
- 执行合法的 epoch promotion。

Scheduler 不重新评价 Supervisor 的语义理由，也不替 Integrator 判断兼容性。

## 6. 统一 Agent Runtime

### 6.1 抽取边界

现有 `ScientistAgent` 同时承载了通用运行机制和 Scientist 专属语义。本次抽取一个
最小的通用循环：

```text
AgentRuntime
├── model invocation
├── JSON action-envelope parsing
├── tool dispatch
├── terminal-action detection
├── step / time / token budget
├── live-context compaction
├── session append / resume
├── trace and usage accounting
└── common error mapping
```

Runtime 不知道 Node、Proposal、Supervisor 或 Integrator。角色语义由小型协议注入：

```python
class AgentRole(Protocol):
    def build_context(self, task, session): ...
    def build_tools(self, task): ...
    def handle_terminal(self, action, state): ...
```

`handle_terminal` 直接返回角色的 typed result。MVP 不增加通用 lifecycle hook；角色差异
留在角色类中，公共循环只处理真正相同的行为。

### 6.2 Role Profile

每个角色向 Runtime 提供：

- identity prompt；
- task context builder；
- tool registry；
- terminal action 集合和校验器；
- session policy；
- 默认 budget 和 context policy。

不采用完整 JSON 声明式 agent framework，也不让 Supervisor/Integrator 继承
`ScientistAgent` 后覆盖大量方法。

### 6.3 Session policy

角色复用 Runtime，但不共享记忆语义：

- Scientist：沿 cognition lineage 持久；
- Supervisor：每次根据当前 `GroupSnapshot` 无状态运行，不积累独立认知历史；
- Integrator：一个 integration request 对应一个临时 session；失败重试恢复同一
  session，新的 request 不继承旧 session。

Supervisor 的组织连续性由 L2 中的 Experiment、allocation、epoch 和 integration
记录提供。不给它增加 dossier 或私人长期记忆，避免重复事实源和路径依赖。

### 6.4 包和兼容边界

现有 `proposer/runtime.py` 表示 Apptainer execution boundary，不应被改造成认知循环。
新的共享循环使用明确名称，例如 `agent_runtime.py` / `agent_loop.py`。

MVP 不强制重命名整个 `proposer` package。先抽取共享 primitive，并保留
`ProposerOrchestrator` 和现有 result envelope 作为兼容层；待三个角色稳定后，再单独
评估 package 命名和目录迁移。

## 7. Scientist 的 EXPLORE 与 SYNTHESIZE

### 7.1 互斥终止协议

一个普通 Scientist research episode 最终只能选择以下之一：

```text
submit_explorations(proposals=1..proposal_slots)
submit_synthesis(proposal=1, donor_experiment_ids=1..n)
abstain(reason, blocking_unknown?)
```

`EXPLORE` 和 `SYNTHESIZE` 不能在同一 episode 中混合。

### 7.2 EXPLORE

EXPLORE 用于扩大可检验方向空间：

- 一次可以提交多个 proposal；
- 每个 proposal 必须对应值得单独支付实验成本的不同机制或因果判断；
- 已验证实现的直接搬运不属于探索；
- 公共知识可以启发方向，但不能让所有新方向自动继承赢家实现。

proposal 可以在 rationale 中说明它来自当前调查、跨 branch 启发或 evidence follow-up，
但这些是自然语言研究判断，不增加枚举字段或新的 lineage。

### 7.3 SYNTHESIZE

SYNTHESIZE 用于把一个或多个已检查 donor Experiment 的成果带入当前 world：

- 只能提交一个 proposal；
- 必须显式引用 donor Experiment；
- 可以包含必要兼容性适配；
- 不能夹带与 donor 统合无关的新优化机制；
- 若不兼容，应 abstain，而不是强行组合；
- 必须重新经过 Executor、Eval 和 Gate。

普通 SYNTHESIZE 是局部 Scientist 的自主研究操作，不等于 epoch integration。

## 8. Supervisor 资源决策

### 8.1 输入快照

Scheduler 为 Supervisor 提供带 watermark 的 `GroupSnapshot`，至少包含：

- 当前 epoch root；
- 所有 eligible Node 的 parent、depth、SHA、status；
- objective metrics 和 gate facts；
- changed paths、来源 Experiment 和 lineage；
- proposer allocation / proposal / experiment 使用量；
- 近期成功、失败、no-change 和停滞记录；
- 跨 branch coverage；
- 已完成 integration request 及结果；
- 可按需深入查询的公开 ResearchState 和 Experiment refs。

Snapshot 默认不推送所有 ResearchState 全文。Supervisor 可主动查询，以减少某一套
叙事在启动上下文中过度占据注意力。

这里的 `eligible Node` 是机械集合：Node 已通过产生它的 Gate、没有被标记为 `dead`、
仍有 proposal capacity，且没有冲突的 open allocation。它不要求 Node 位于 Frontier；
因旧 Frontier 逻辑成为 `dormant` 的有效 Node 也必须出现在 Supervisor 可查询范围内。

Supervisor 和 Integrator 的调查范围更广，但不绕过 Knowledge Exchange 的二次访问
纪律：先检查具体 Experiment，才能读取其 originating ResearchState。扩大的是可查询
world 范围，不是把所有人的解释自动灌入上下文。

### 8.2 决策输出

Supervisor 输出一个 `SupervisorDecision`：

```text
decision_id
epoch_id
snapshot_watermark
allocations:
  - node_id
    proposal_slots
integration_request: optional
rationale
evidence_refs
```

每个 allocation 是有限 lease，不是永久生存资格。未获资源的 Node 仍在 Archive，
后续 Supervisor 可以重新选择。

当 `allocations` 为空且没有 integration request 时，rationale 说明当前无值得启动的工作；
Scheduler 继续使用现有 quiescence 判断，不增加 Supervisor 专用状态。

Node 相似性和暂停理由只进入本次 decision 的 rationale / trace，不建立相似组状态机。

当成功的 integration candidate 等待晋升评审时，同一 Supervisor Role 输出单独的
`EpochDecision(request_id, promote | retain, rationale, evidence_refs)`；它不与普通资源
allocation 混成一个 terminal result。

### 8.3 选择纪律

Supervisor 的身份目标是最大化有限预算下的边际研究价值和未来可利用选项，而不是
预测唯一赢家。它必须同时考虑：

- 当前收益能否继续复利；
- Node 是否代表不同机制或不同适用边界；
- 新实验能否减少关键未知性；
- 多个 Node 是否高度重复；
- 某 lineage 是否仅因低基数而被饿死；
- 当前分配是否过度集中；
- 是否已经出现互补且成熟的阶段成果。

相似判断可以使用 ancestry、changed paths、diff、metrics 和公开 research rationale，
但必须输出引用和可审计理由。MVP 不增加自动相似度模型。

### 8.4 调用时机

Supervisor 不逐 token 或逐 Scheduler tick 实时决策。Scheduler 在以下情况请求新的
decision：

- proposer capacity 可用，且没有尚可执行的有效 allocation lease；
- 上一 decision 已消费完；
- 新 Experiment 使 snapshot watermark 变化并使未执行 decision 失效；
- 系统即将 quiesce；
- integration experiment 返回，需要判断 epoch promotion。

一个 decision 可以一次填充当前可用的多个 proposer slot，但 Supervisor 不为这些
Scientist 指定研究操作。

### 8.5 Frontier 降级

Supervisor 是正常路径唯一的资源准入者：它授予 Node Scientist lease；由该 lease 的
reserved proposal id 合法发布的 Proposal 已经完成准入，不需要 Frontier 在 Executor
前再次审核。Executor queue 只保留 FIFO、容量上限和 overflow dormant 等机械背压，
不能因为 Proposal parent 不在当前 Frontier 而把它转为 dormant。

Frontier 只保留两个用途：

- 用于资源集中和搜索健康度遥测；
- 在 Supervisor job 失败、超时或输出非法时提供确定性 fallback。

Fallback 只保证系统继续运行，不与有效 Supervisor decision 同时竞争资源。
Fallback 产生的 lease 与 Supervisor lease 使用同一发布和 Executor queue 路径；
Frontier 本身不直接决定 Proposal 是否执行。

## 9. Integration 与 Epoch

### 9.1 启动条件

Supervisor 可以在以下模式同时出现时创建 integration request：

- 多条不同 lineage 均已有 gate-passed 阶段成果；
- 分支之间存在可解释的互补可能；
- 搜索已经明显分叉，公共收益仍停留在私人 lineage；
- 新实验开始重复已有区域，继续单纯发散的边际信息下降；
- 距离上一次 epoch promotion 已积累足够新证据。

这些是判断信号，不是要求全部满足的固定公式。Supervisor 必须引用具体事实，且可以
判断当前尚不适合统合。

### 9.2 Integration request

请求至少包含：

```text
integration_request_id
epoch_id
target_node_id
donor_experiment_ids
selection_rationale
```

- `target_node_id` 是唯一实现基线，其 Node SHA 同时决定 Integrator 的只读 workspace
  和后续 Executor 的可写 experiment workspace；donor SHA 不参与 checkout，也不形成
  Git merge parent；
- donor 必须是 Gate 通过、拥有有效 Child Node 的已完成实现 Experiment；失败和负面
  Experiment 可以作为普通 evidence 引用，但不能伪装成待移植 donor；
- donor 是 provenance，不是结构父节点；
- request 指定需要调查的成果集合，但不预写技术统合方案。

### 9.3 Integrator 输出

Integrator 生成自己的 ResearchState，并终止为：

```text
submit_synthesis(proposal, donor_experiment_ids)
```

或：

```text
abstain(reason)
```

兼容性、冲突、纳入/排除和实现顺序按需要写入现有 proposal rationale / instruction；
不为它们建立强制结构化字段。Integrator 不能引用 request 之外的 donor。

### 9.4 Candidate epoch Node

Integrator 在 target Node SHA 的只读 workspace 中调查并形成 proposal；它不生成一个
由多个 SHA 自动合成的中间世界。Executor 随后从同一 target Node SHA 创建新的可写
workspace，实现 integration proposal。成功 Experiment 创建普通 Child Node：

```text
target Node --structural parent--> candidate Node
donor Experiments --provenance------^ 
```

本次不把 Node 改成多父结构。单父关系继续决定：

- 代码基线；
- diff；
- 回滚；
- 相对收益计算。

donor refs 表达成果来源。真正的 multi-parent Node merge 留到该表示法被实际限制时再做。

### 9.5 Epoch promotion

只有满足以下硬前置条件的 candidate 才能由 Supervisor 提议晋升：

- 来源于当前 open integration request；
- Executor 成功结束；
- correctness Gate 通过；
- Experiment 有有效 result SHA 和 Child Node；
- Supervisor 已查看 integration outcome。

Supervisor 可以根据整体 metrics、退化、收益覆盖和未纳入成果决定 promote 或 retain。
Scheduler 只校验前置条件并持久化合法决定，不能允许 Supervisor 覆盖 Gate failure。

晋升创建逻辑 epoch：

```text
epoch_id
root_node_id
previous_epoch_id
created_at
```

promotion Experiment 沿 `root_node_id -> nodes.experiment_id` 确定性反查，不在 epoch
重复保存。

系统不改写历史 root，也不删除旧 branch。最新 epoch root 是新的默认共同 world；旧 Node
仍可被 Supervisor 选择获得少量资源，从而保留逃离局部最优的通道。

## 10. 持久化模型

### 10.1 必要领域元数据

Proposal 需要持久化以下最小字段，而不能只把它们藏在 rationale 文本中：

```text
research_operation: explore | synthesize | null
donor_experiment_ids: []
```

约束：

- `explore` 不能有 donor；
- `synthesize` 必须有 donor；
- Integrator 产出的 Proposal 仍是 `synthesize`；request 与 Proposal 的关联保存在
  `integration_requests.proposal_id`，不在 Proposal 重复保存 request id；
- 迁移前的旧 Proposal 保持 `null`，不猜测它原本属于探索还是综合；所有新提交必须
  明确选择 `explore` 或 `synthesize`。

### 10.2 组织工作流对象

异步恢复要求以下状态不能只存在于 Supervisor 对话或临时 artifact：

- `epochs`：逻辑共同基线；
- `integration_requests`：target、donors、状态、Integrator episode、proposal、experiment
  和 promotion 结果。

`SupervisorDecision` 不新增领域表：接纳/拒绝结果写入已有 `scheduler_events`，实际资源
lease 继续由 `proposer_allocations` 持久化。Decision artifact 和 watermark 只负责
幂等 ingest 与审计，不形成第二套调度状态。

具体表拆分可以在实现计划中按现有 Store 模式确定，但必须满足：

- identity-first；
- Scheduler 唯一写入；
- job 重试幂等；
- stale result 不覆盖新状态；
- restart 后不依赖 LLM session 才能恢复工作流。

升级旧数据库时，以原始 root Node 创建唯一的 `epoch-0`；该迁移不改变任何 Node parent、
status 或 SHA。后续 epoch 只能通过合法 promotion 创建。

### 10.3 Attempt kind

attempt 层需要区分：

```text
supervisor
proposer
integrator
experiment
```

Integrator 虽复用 Agent Runtime，但其 job contract、terminal result 和恢复身份不同，
不能伪装成普通 proposer attempt。

## 11. Job 与数据流

### 11.1 普通分配

```text
Scheduler builds GroupSnapshot
    -> Supervisor Job
    -> SupervisorDecision artifact
    -> Scheduler validates + persists
    -> Scientist allocation
    -> ScientistResult
    -> Proposal(s)
    -> Executor Experiment(s)
    -> Ledger + Child Node(s)
```

### 11.2 阶段统合

```text
SupervisorDecision(integration request)
    -> Scheduler persists request
    -> Integrator Job
    -> IntegrationResult
    -> one Proposal or abstain
    -> Executor Experiment
    -> candidate Child Node
    -> Supervisor reviews outcome
    -> promote epoch or retain as ordinary Node
```

Supervisor 不在进程内直接调用 Integrator。二者是逻辑 parent/subagent 关系，实际由
Scheduler 通过持久化 artifact 和 job backend 编排，以保持 Condor/local parity。

## 12. 防止 shared brain

Supervisor 的身份 prompt 必须明确：

> 你不替学生解决具体课题，也不要求课题组形成统一 worldview。你的职责是在有限预算
> 下减少重复劳动、保护彼此不同的研究路线、让尚未成熟但有真实信息价值的分支获得
> 机会，并在阶段成果成熟时组织一次独立的统合验证。

除身份内化外，系统使用以下结构保护：

1. Supervisor 不读取 Scientist 私有 L3；
2. Supervisor 分配 Node，不向 Scientist 注入技术指令；
3. Scientist 自主选择 `EXPLORE` / `SYNTHESIZE`；
4. allocation 是有限 lease，暂停 Node 可复活；
5. 资源决定和 promotion 记录 evidence refs；
6. Integrator 是新临时身份，不继承 donor 私有 cognition；
7. Frontier 只用于搜索健康度遥测和故障 fallback；
8. Supervisor 无私人长期记忆，每次从当前 L2 事实重新判断；
9. Harness facts 始终优先于任何组织级叙事。

## 13. 错误、重试和恢复

- Supervisor timeout / infra failure：记录 attempt，按现有策略重试；在重试耗尽或需要
  保持吞吐时使用 Frontier fallback；
- Supervisor 输出未知、dead、预算耗尽或已有 open allocation 的 Node：Scheduler 拒绝
  对应项，记录 contract error，不执行隐式替代；
- decision watermark stale：未执行 lease 失效，重新请求 decision；已开始 job 正常完成，
  结果按其原 allocation identity 入库；
- donor Experiment 不存在、未完成或 Gate 未通过：拒绝 integration request；
- Integrator infra failure：恢复同一 request 和 session；
- Integrator abstain：关闭 request，保存冲突说明，不创建 Proposal；
- integration proposal 越界或 donor 不一致：拒绝 ingest；
- Executor / Eval infrastructure failure：继续使用现有 attempt 语义；
- integration Gate rejected / no change：关闭本次 request，保留负面证据，不晋升；
- promotion 时 candidate 已失效或 provenance 断裂：拒绝 promotion；
- Scheduler restart：从 DB 恢复 current epoch、open integration request、allocation 和
  attempts；Supervisor decision 从 `scheduler_events` 审计，不形成待恢复状态机。

## 14. 遥测

新增遥测只用于审计和后续改进，不自动变成新的 fitness：

### 14.1 Scientist operation

- EXPLORE / SYNTHESIZE / abstain 次数；
- EXPLORE proposal slots 使用率；
- donor 数量和跨 lineage 比例。

### 14.2 Supervisor allocation

- 每个 decision 的 allocation 分布；
- lineage 和 Node 的实际资源份额；
- allocation concentration / entropy；
- Supervisor decision 与 Frontier fallback 使用率。

### 14.3 Integration / epoch

- integration request 频率；
- Integrator abstain、Gate reject、no-change 和成功率；
- donor 数量和 lineage 数量；
- epoch 间 objective 变化；
- promotion 后旧 lineage 获得资源和产生突破的比例。

## 15. 测试与成功标准

### 15.1 Agent Runtime 等价性

- 抽取 Runtime 后，现有 Scientist prompt、tool、terminal、session、trace 和 proposal
  行为保持兼容；
- context compaction 继续按完整 action/observation pair 工作；
- 不同 Role 的工具和 terminal action 严格隔离；
- Role contract error 不泄漏为其他 Role 的合法结果。

### 15.2 Scientist protocol

- EXPLORE 可以提交多个 materially distinct proposal；
- EXPLORE 不能携带 donor implementation；
- SYNTHESIZE 只能提交一个 proposal；
- SYNTHESIZE 必须引用已检查且有效的 donor Experiment；
- 一个 episode 不能混合两种终止动作；
- operation 和 provenance 被持久化并可查询。

### 15.3 Supervisor

- Snapshot 覆盖 Frontier 外的 eligible Node；
- Supervisor 可以选择低绝对分数但不同 lineage 的 Node；
- 相似组只执行代表 allocation；
- 未选 Node 保留在 Archive，可被后续 decision 复活；
- stale/非法 decision 被拒绝；
- Supervisor failure 时 Frontier fallback 可继续分配；
- Supervisor 选择的非 Frontier Node 所发布的合法 Proposal 仍能进入 Executor；
- Frontier 不能把已通过 lease 准入的 queued Proposal 转为 dormant；
- Supervisor 输出不能包含普通 Proposal 或 Scientist 技术指令。

### 15.4 Integrator 与 epoch

- Integrator 能查看 target 和指定 donor 的公开材料，但不能查看 L3；
- Integrator 每次新 request 使用新身份；
- Integrator 是 synthesis-only Scientist，不能在 integration request 中切换到 EXPLORE；
- Integrator 只输出一个 integration proposal 或 abstain；
- Integrator 与执行其 Proposal 的 Executor 都从 request 的 target Node SHA 建立 workspace；
- donor provenance 与 request 必须一致；
- integration 仍通过普通 Executor、Eval 和 Gate；
- Gate reject 不能晋升；
- 成功 candidate 保持单 structural parent 和多 donor provenance；
- promotion 只移动逻辑 epoch root，不改写历史 Node；
- restart 能恢复 open integration request 和 current epoch。

### 15.5 运行成功标准

首次真实运行至少验证：

- 相似高分 Node 不再自动同时占满 proposer 资源；
- 至少一条低基数但机制不同的 branch 获得继续研究机会；
- 普通 Scientist 仍产生彼此不同的 worldview；
- Supervisor 没有把同一技术指令广播到各 lineage；
- 至少一次 integration request 能安全 abstain、失败或产出 candidate Node；
- promotion 后新探索默认从共同基线开始，同时旧 branch 仍可恢复。

## 16. 与旧设计的关系

本设计显式修订 Research Knowledge Exchange spec 的以下旧约束：

- “Knowledge Exchange 不改变 Frontier scoring 或 research budget”改为：知识视图仍不
  推荐方向，但 Supervisor 可以基于全局事实和 attributed memo 分配 research budget；
- “不增加 Synthesis workflow 类型”改为：普通 Scientist 增加明确的 SYNTHESIZE
  operation，阶段统合增加独立 integration request；
- “不存在 branch merge operation”改为：仍不存在直接 Git merge 或多父 Node，但允许
  Integrator 通过 Proposal -> Experiment 创建带 donor provenance 的综合 Child；
- Frontier 从唯一资源选择器降级为统计视图和失败 fallback，并退出 Executor queue
  的正常 Proposal 准入路径。

以下边界保持不变：

- Experiment Ledger 是事实权威；
- ResearchState 是署名且可修正的判断；
- current Scientist 不能直接采用 sibling ResearchState；
- 跨 branch 成果必须通过新 Experiment 验证；
- Tree 历史只增不删；
- Scheduler 是唯一 L2 writer；
- Executor 和 Gate 是现实反馈边界。

## 17. MVP 分界

为避免大工程失控，第一版只建立必要闭环：

1. 抽取统一 Agent Runtime，并让 Scientist 等价迁移；
2. 加入 EXPLORE / SYNTHESIZE 协议和 provenance；
3. 加入 Supervisor decision、持久化和 Frontier fallback；
4. 让 Supervisor 独占正常 proposer Node 选择，并移除 Frontier 对已准入 Proposal 的
   Executor 二次否决；
5. 加入临时 Integrator role 和 integration request；
6. 用现有 Executor 验证 integration proposal；
7. 加入逻辑 epoch root 和 promotion；
8. 加入恢复、测试和必要遥测。

任何多 agent 会议、自动 patch 合并、multi-parent Node、复杂调度算法和 package 全面
重命名都不属于本 MVP。

## 18. 设计不变量

1. Scientist 自主选择 EXPLORE、SYNTHESIZE 或 abstain。
2. Supervisor 选择 world 的资源资格，不替 Scientist 选择研究操作。
3. Integrator 是一次性 synthesis-only Scientist，只提出一个统合实验，不直接实现或
   晋升，也不在统合任务中转做 EXPLORE。
4. Executor 保持 `claude -p` 路径，不进入统一 Agent Runtime。
5. Supervisor、Scientist、Integrator 复用同一通用对话循环，但使用独立 Role、工具、
   terminal protocol 和 session policy。
6. Supervisor 和 Integrator 不读取 Scientist 私有 L3。
7. Harness fact 与 LLM judgment 始终分层并带 provenance。
8. Frontier 只作为统计视图和失败 fallback；正常路径的 Scientist lease 与 Executor
   Proposal 准入都由 Supervisor decision 派生。
9. Supervisor decision 是有限 lease，不删除未选 Node。
10. 普通 SYNTHESIZE 和 epoch integration 都必须通过新的 Experiment。
11. integration candidate 只有一个 structural parent，donor 只形成 provenance DAG。
12. Gate rejected 的 candidate 永远不能晋升 epoch。
13. epoch promotion 不改写历史 Tree；旧 branch 保留复活能力。
14. 所有异步角色结果 identity-first、可幂等 ingest、可在 Scheduler restart 后恢复。
15. Scheduler 保持唯一 SQLite writer 和实际 job orchestrator。
16. Integrator 和执行其 Proposal 的 Executor 使用同一个 target Node SHA；donor 只提供
    evidence 与 provenance，不自动改变 workspace 基线。

## 19. 最终原则

> SimpleEvolution 不再只靠一组机械指标让树无限向外分叉。独立 Scientist 负责制造
> 真正不同的研究方向；Supervisor 负责管理课题组的有限注意力；Integrator 在阶段成果
> 成熟时把多个 branch 写成一个新的可验证世界；通过现实检验的综合成果形成下一段树干，
> 而全部历史、失败证据和替代路线继续留在树中。
