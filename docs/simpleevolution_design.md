# Research Evolution Harness 设计文档

# 0. 组件总览

整个系统围绕五组核心组件工作。

## 0.1 三层数据层：L1 / L2 / L3

系统的全部"记忆"，按保真度和用途分层，由 identity（§2.4）贯穿：

- **L1 Trace Store**：每次 invocation 的完整轨迹（输入 context、消息流、tool calls、stdout/stderr、build/eval log、artifact）。append-only、immutable，每条记录标注所属 Thread / Proposal / Experiment / Attempt。回答"实际上到底发生了什么"。注意 L1 要摄入两种异构 trace（Scientist runtime 的 tool loop 与 Executor `claude -p` 的 stream-json），需统一 envelope（invocation_id / role / identity refs / events / output refs），payload 保留原生保真度。
- **L2 SQLite Research DB**：Node / Proposal / Experiment / Attempt / Thread / 调度状态，identity 互引。回答"Research Tree 现在是什么状态"。**唯一 writer 是 Scheduler**（§4）。
- **L3 Scientist 认知**：notebook、beliefs、expectations、对实验的解释。Thread 私有、有损压缩、可在 (Thread, Proposal) 时刻快照。回答"这个 Scientist 现在怎么理解它的研究"。

三条关键规则：L1 不是 Agent 默认上下文（按需 drill-down）；L2 是索引、L1 是原始证据；L3 不共享——跨 Thread 信息只能经 L2 事实流动（认知不共享，现实共享，§8）。

## 0.2 演化状态机：异步 Scheduler + 两类 Pool

Scheduler 是唯一的长期中央控制器，不推 round，只响应事件（Node created / Proposal created / Experiment finished / Job failed / …）。它只做两类资源分配，不做任何科学判断：

```text
Frontier Node      ──分配──►  Proposer capacity（逻辑配额 P）
Queued Proposal    ──分配──►  Executor capacity（逻辑配额 E）
```

Pool 是逻辑并发配额，不是常驻进程：Scheduler 决定哪份 research work 值得一个 job，HTCondor 决定 job 在哪台机器跑。

四条承重语义：Experiment identity ≠ Attempt（§16）；infra failure ≠ scientific failure（§17）；关键状态跃迁原子化（§20）；Resume = reconciliation，恢复研究状态而非旧进程（§18）。

Scheduler 唯一带"演化算法味道"的职责是 Frontier Selection（§7.2），但它是基于 measured axes 的纯机械计算，不是科学判断。

## 0.3 核心对象与 identity 模型

| 对象 | 定义 | 性质 |
| --- | --- | --- |
| Node | 一个确定的 Research World（identity、parent、source SHA、metrics、gate、depth） | 事实对象，不携带"方向好不好"的判断 |
| Tree / Archive | Node 全集及父子关系；物理上是 git DAG | 保存多个 research worlds——worse but valid 的 Node 合法存在（§2.3） |
| Frontier | 从 Archive 机械计算出的资源分配视图（per-axis winners 的并集） | 资源状态，不是 Node 的属性；动态重算 |
| Proposal | Scientist 在某 Node 上的待验证判断，属于 Thread × Node | 认知，尚未成为事实 |
| Experiment | 一次逻辑科学验证（Proposal → result SHA、metrics、gate、Child Node） | 完成即事实，哪怕结果不好 |
| Attempt | Experiment 的一次实际执行 | 基础设施层概念，挂在 Experiment identity 下 |
| Scientist Thread | 一条认知 lineage，可快照、可 fork、可被任意空闲 Proposer Job 恢复 | 认知 identity，≠ 进程 / 机器 / Node |

评价 Node 的两个维度分离：**Gate = world validity**（是否仍属于允许研究的问题空间），**Objective = world quality**（表现如何；变差不剥夺合法性）。

## 0.4 两个 Agent Role：由四元组区分职责

系统只有两个 agent role，职责区分完全来自四个维度的组合：

| | Proposer（局部智能） | Executor（无认知） |
| --- | --- | --- |
| Runtime / Charter | Scientist runtime 常规科研模式 | `claude -p` coding agent |
| 看到什么 | 自己的 Node World + 自己的 L3 + 按需查 L2；看不到别人的 pending proposal / 热点 branch | Proposal 文本 + parent SHA 的 worktree + gate 定义 |
| 工具 | 本地调查（shell / read / search / git）+ L2 只读查询 + 必要 drill-down L1 | Read / Edit / Write / Bash，仅限自己的 worktree |
| Workspace | Node World（客观、只读）+ Scientist Lab（私有认知空间） | 一次性 worktree，做完即弃 |
| 产出 | Proposals + L3 snapshot | commit SHA（或失败原因） |

刻意的信息不对称（防认知热点坍缩）：Proposer 知道"现实已经证明过什么"，不知道"别人正准备做什么"。

早期设计中的第三个 role——Reviewer（全局语义过滤）——已整体移入 Parking Lot（§24）：它预防的 failure mode 尚未被观察到，而其阀门职能由 GEPA 式 Frontier Selection 在上游机械完成（§7.2）。

## 0.5 单次实验链路：Executor → Eval → Gate

基本复用 SimpleLoop 最成熟的资产（worktree 隔离、harness-owned commit、确定性 diff gate、harness-owned eval 与 KEY=VALUE metric 解析）：

```text
Proposal + parent SHA
  → worktree（从 parent SHA 切出）
  → Executor 在 worktree 内改代码（不许自行 git commit）
  → 硬 Gate：diff 只碰 editable、不碰 frozen（确定性，无 LLM）
  → Harness 自己 stage + commit → result SHA
  → Eval：harness 跑 eval commands，解析 KEY=VALUE（含全部 measured axes）
  → Gate metrics 布尔化 → world validity 判定
  → 原子落库：Experiment 完成 + Child Node 创建（同一逻辑事实）
```

相对 SimpleLoop 的三个语义变化：

1. **Judger 角色消失**。Tree 里没有 incumbent、没有 winner selection，Experiment 产出纯事实（SHA / metrics / gate）。Judger 留下的遗产是 harness-owned metrics 原则（harness 算数，LLM 不估数），转移到 Scientist 查询实验历史时的呈现方式；`risk` 这类 LLM 判断不再混进 Node 事实。
2. **Gate 语义升级**：从候选门槛变为 world 合法性定义（正确性 / 物理等价 / 容差 / 接口不变量），objective 不参与合法性。
3. **Worktree 生命周期反转**：SimpleLoop 用完即删、单链推进；Evolution 里 Node 长期存在于 git DAG，worktree 按需创建。

## 0.6 一次 Experiment 的完整生命周期

```text
1. Experiment 完成 → Scheduler 原子落库：New Node + 事实（L2），trace 归档（L1）
2. New Node 通过 gate → 进入 Archive；Scheduler 重算 Frontier（§7.2）
3. Child Thread 从 Proposal 提交时的 L3 快照 fork
4. Scheduler 从 Frontier 按 f-weight 分配 proposer capacity → Proposer Job
5. Scientist 研究自己的 world，产出 Proposals（完整形成才进 L2）
6. Proposals 进 Executor Queue（有界、机械规则，§12）
7. Executor slot 空出 → 取队头 → Experiment Job（链路见 §0.5）
8. Experiment 完成 → 回到 1
```

终止只有三种：budget 耗尽 / 人工停止 / quiescence（§21）。

---

## 1. 设计目标

下一阶段不再继续围绕单线程 Scientist 或 SimpleLoop 的 round-based loop 做深化，而是重新设计一套面向**大规模异步研究演化**的 Harness。

系统不再采用：

`Round → Proposer → Candidates → Executor → Best → Next Round`

而采用持续事件驱动的演化模型：

`Node → Proposer → Proposal → Experiment → New Node`

研究过程不再依赖统一 round。只要新的有效 Node 出现，它就可能进入 Frontier 并获得后续研究；只要新的 Proposal 出现，就进入实验资源排队；只要实验结束，就立即形成新的研究事实并推动树继续生长。

目标不是维护一条最优链，而是维护一棵持续扩展的 **Research Tree**，允许多个不同方向同时存在、深化和竞争。

---

## 2. 基本设计原则

### 2.1 Harness 管理研究世界，不替 Scientist 做科学判断

系统中的职责应严格分开：

- **Proposer / Scientist**：从一个具体 Node 出发，判断接下来值得尝试什么。
- **Executor + Eval + Gate**：回答现实中实际发生了什么。
- **Scheduler**：只负责有限计算资源分配、Frontier 的机械计算、状态推进和作业生命周期。

Scheduler 不决定某个科学方向是否正确，也不承担科研 reasoning。

**系统中没有全局科学判断者。** 全局层面的方向控制由 measured-axes Frontier Selection 机械完成（§7.2）：一个研究方向是否继续获得资源，取决于它是否还能在某个 reality-defined 的轴上赢，而不取决于任何 LLM 的偏好。

---

### 2.2 事实与判断必须分开

Research Tree 中存在多种性质不同的信息：

- Node 的 SHA、metric、gate result 是事实；
- Experiment outcome 是事实；
- Node 的 axis-winner 状态是事实的机械计算结果；
- Scientist 的 hypothesis 是认知；
- Frontier membership、Scheduler priority 是资源分配状态。

这些信息不能混成一个统一的“score”。

---

### 2.3 Tree 保存的是多个 Research Worlds，而不只是多个优秀结果

一个通过基本有效性要求的实验结果，即使 objective 暂时变差，也可能形成一个新的 Node。

例如：

```text
N0
├── N1  better
├── N2  worse
└── N3  similar
```

N2 并不因为暂时变差就失去留在 Archive 中的资格。

否则所谓 Tree 仍然只是 greedy hill climbing。

因此需要明确区分：

- **Node 是否是合法 research world**（Gate 决定，永久）
- **Node 是否值得继续获得研究资源**（Frontier 决定，动态）

前者属于实验事实，后者属于演化策略。

与此对应，评价一个 Node 的两个维度也要分开：

- **Gate = world validity**：回答这个世界是否仍属于允许研究的问题空间（正确性、物理等价、数值容差、接口不变量、任务约束）。Gate 不回答表现好坏。
- **Objective = world quality**：回答这个世界表现如何。Objective 变差不构成 Node 失去合法性的理由。

---

### 2.4 Identity 是基础架构，不是 Resume 的附属品

一个复杂演化系统必须首先明确自己的最小单元，并且每个层级的最小单元都拥有自己的身份证：

- Node / Proposal / Experiment / Scientist Thread 是 L2 的身份证对象；
- Execution Attempt 挂在 Experiment identity 之下；
- Proposer / Executor 的每次 invocation 也有自己的 identity。

所有存储层必须携带这些身份证：

- L1 的每一条 trace 都标注它属于哪个 Thread / Proposal / Experiment / Attempt；
- L2 的记录之间相互以 identity 引用；
- L3 的 snapshot 绑定 (Thread, Node, Proposal) identity。

Identity 先行意味着：系统中任何一条信息都能回答"它属于谁、由什么产生"。Resume、审计、可视化、跨层 drill-down 都是这一原则的自然结果，而不是各自单独设计的机制。这一层必须在基础架构设计时就定死，事后无法补上。

---

## 3. 核心研究对象

整个系统围绕以下几个长期对象组织。

### 3.1 Node

Node 表示一个确定的 Research World。

它至少具有：

- 唯一 Node identity
- Parent Node
- 产生它的 Experiment
- Exact source SHA
- 当前 objective / metrics（含全部 measured axes）
- Gate result
- Tree depth
- 当前研究状态

Node 是事实对象。

Node 本身不保存“这个方向好不好”的主观判断。

---

### 3.2 Proposal

Proposal 是 Scientist 在某个 Node 上形成的待验证研究判断。

它属于：

`Scientist Thread × Node`

而不是全局独立存在。

Proposal 至少表达：

- 要做什么
- 为什么值得尝试
- 基于什么现有理解
- 预期可能发生什么
- 什么结果会削弱原有判断

Proposal 尚未成为实验事实。

---

### 3.3 Experiment

Experiment 表示一次逻辑上的科学验证。

关系为：

`Parent Node → Proposal → Experiment → Child Node`

Experiment 保存：

- 所属 Proposal
- Parent Node
- 执行结果
- Result SHA
- Metrics
- Gate result
- 是否产生 Child Node

必须区分：

**Experiment identity** 和 **Execution Attempt**。

网络错误、API 余额不足、Condor evict 等只产生新的 Attempt，不产生新的科学 Experiment。

---

### 3.4 Scientist Thread

Scientist Thread 表示一条持续的认知 lineage。

它不是：

- 一个进程
- 一台机器
- 一个 HTCondor Job
- 一个 Node

它表示某个 Scientist 持续发展的研究认识。

Scientist Thread 可以：

- 从 Parent Node 延续到 Child Node；
- 在多个 Child Node 出现时 fork；
- 被任意空闲 Proposer Job 恢复。

因此：

`Proposer Worker = 计算资源`

`Scientist Thread = 认知 identity`

`Node = 客观 Research World`

三者必须分离。

---

## 4. Research Database

复杂树演化不再适合仅依赖 JSONL。

系统采用 SQLite 作为 **L2 Research State 的 source of truth**。

SQLite 负责表示当前 Research Evolution 世界，包括：

- Nodes
- Proposals
- Experiments
- Attempts
- Scientist Threads
- Frontier / axis-winner 状态
- Job / scheduling states

它应当能够直接支持：

- Tree reconstruction
- Node lineage 查询
- Experiment 查询
- Proposal 状态查询
- Frontier 计算与健康度遥测
- 当前运行任务查询
- 实时可视化
- Resume / reconciliation

SQLite 保存的是**结构化研究语义**，不是所有 Agent 原始上下文。

**SQLite 只有一个 writer：Evolution Scheduler。**

远端 Job（Proposer / Executor）不拥有 Research DB write authority。Job 只把结果写成 durable artifact；Scheduler 校验后统一 ingest 为 L2 状态。Resume reconciliation（见 §18）就是这条唯一写入路径的恢复形式。这延续了整个系统的信任边界：Agent 提交结果，Harness 持有事实世界。

---

# 5. 三层历史体系

整个系统的历史应明确分为 L1 / L2 / L3。

---

## 5.1 L1：Full-Fidelity Trace Store

L1 保存整个系统发生过的全真轨迹，并持久化在本地。

它是：

> “实际上到底发生过什么”的最高保真记录。

包括所有：

- Proposer invocation
- Executor invocation
- 实际发送给模型的 context
- Agent messages
- Tool calls
- Tool results
- stdout / stderr
- Build logs
- Eval logs
- Gate logs
- Output artifacts
- Execution attempt 信息

L1 应保持 append-only / immutable 语义。

它用于：

- 完整审计
- 深度 history drill-down
- Debug
- Resume
- 研究行为分析

L1 不应成为每次 Agent 调用默认注入的上下文。

---

## 5.2 L2：Research DB

L2 是 SQLite 中的结构化 Research State。

它回答：

> 当前 Research Tree 是什么状态？

例如：

- N42 是从哪个 Experiment 产生的？
- 哪些 Proposal 在排队等待执行？
- 哪些 Experiment 正在执行？
- 当前 Frontier 由哪些 Node 组成？
- 某个 branch 已经验证了什么？

L2 可以引用 L1 的 trace reference。

典型关系：

```text
Proposal P183
    → proposer_trace_ref

Experiment E91
    → executor_trace_ref
    → eval_trace_ref
```

因此：

**L2 是结构化索引，L1 是完整原始证据。**

---

## 5.3 L3：Scientist Private Research State

L3 表示 Scientist 自己的认知连续性。

例如：

- Notebook
- 当前理解
- Beliefs
- Important unknowns
- Expectations
- 对过去实验的解释
- 当前 working model
- Cognitive snapshot

L3 不等于 transcript。

它是 Scientist 对研究过程的有损认知压缩。

可以将三层理解为：

- **L1：摄像机录像**
- **L2：实验数据库**
- **L3：科学家的研究笔记和当前认识**

---

# 6. Evolution Scheduler

Scheduler 是整个 Harness 的长期中央控制器。

它不推进 round，而响应持续发生的状态变化。

基本事件包括：

- Node created
- Proposer available
- Proposer finished
- Proposal created
- Executor available
- Experiment finished
- Job failed
- Job resumed

Scheduler 主要完成两类资源分配：

`Frontier Node → Proposer capacity`

以及：

`Queued Proposal → Executor capacity`

外加一项机械计算：每次 Experiment 完成后重算 Frontier（§7.2）。

Scheduler 本身不承担科学判断。

---

# 7. Node → Proposer 演化

一个新的有效 Node（通过 gate）产生后进入 Archive（Research Tree），Frontier 随即被重算。

GEPA 语义取代了早期"每个 Node 默认被 Proposer 消费一次"的设计：

- **不是每个 Node 都获得 Proposer**。有限的 proposer budget 从 Frontier 中分配（§7.2）；
- **Frontier 中的 Node 可以被多次研究**，直到它失去所有 axis winner 地位；
- **不在 Frontier 的 Node 留在 Archive 中**，作为其它路线的正面参考 / 反面材料，事实永远可查；
- 如果未来需要重新探索 Frontier 之外的 Node，应作为独立的 branch revival 机制，而不是默认行为。

---

## 7.1 Root Node

Root 是特殊 Node。

初始阶段 Frontier 尚未分化，需要主动铺开搜索空间，因此 Root 可以被多个独立 Fresh Scientists 同时研究：

```text
Root
├── Scientist T1
├── Scientist T2
├── Scientist T3
├── Scientist T4
...
```

这样 diversity 来源于多个独立 cognition，而不是让同一个 Scientist 强行生成大量表面不同但实际上高度相关的 Proposal。

## 7.2 Frontier 管理：GEPA 式 per-axis winner sets

树以 1→3→9→27 的方式展开时，叶子会指数增长，远超 Proposer / Executor 容量。解决办法不是在出口处审查 Proposal，而是**控制演化注意力的入口**：只有 Frontier 中的 Node 有资格获得 proposer capacity。

### Archive ≠ Frontier

- **Archive（Research Tree）**：保存所有 valid Node——只要通过 gate（world validity），无论 objective 如何。Tree 是历史结构，只增不删。
- **Frontier**：从 Archive 机械计算出的资源分配视图，决定谁获得 proposer capacity。

两者绝不混淆：Tree 回答"我们发现过什么"，Frontier 回答"当前谁值得获得 evolution budget"。

### Frontier 的构造（measured-axes winner union）

Eval 必须产出多个 reality-defined 的 measured axes。OMILREC v0 采用 pipeline stage 轴：

```text
total_ms / qmle_ms / tmle_ms / qtmle_ms / energy_ms
```

对每个轴 a，维护当前最优 Node 集合 F_a（允许统计 tie）：

```text
Frontier = ∪ (F_total, F_qmle, F_tmle, F_qtmle, F_energy)
```

一个 Node 同时赢多个轴时仍只占一个 Frontier 位置。

### Tie 与 hysteresis

计时有噪声。tie band = benchmark 噪声底；现任 axis winner 除非被 challenger 以超过噪声 margin 击败，否则保位（hysteresis）。防止 Frontier 随测量抖动翻来覆去、搅动 proposer 分配。

### Proposer 分配：f-weighted sampling

Node 赢得的轴数记为 f[N]。proposer capacity 按 f 成比例随机分配：

- 广泛最优的 Node 获得更多注意力（exploitation）；
- 单轴专精的 Node 不被 global average 杀掉（exploration）。

### 这如何替代 Reviewer

GEPA 式 Frontier 机械地完成了早期设计中 Reviewer 的三项职能：

- **热点坍缩**：axis winner 由测量决定，churn 快的分支买不到注意力；
- **惯性死磕**：连续 marginal variants 无法赢得任何轴 → Child 不进 Frontier → 分支被结构性断粮；
- **分支"死亡"**：= 掉出 Frontier（dormant）。事实永存 L2，revival 通道常开。

### Frontier 健康度遥测与轴的升级

Scheduler 机械统计：|Frontier|、单 lineage 占轴数、**每个 Node 实际获得的 proposer 分配分布**（f-weight 是概率不是配额——多轴 winner 可能把单轴专精 Node 的实际频率压到接近饿死，这个分布必须可观测，它是 Parking Lot 中 ε-exploration 是否复活的判据）。若 |Frontier| 长期 ≈ 1~2，或单一 lineage 长期占满所有轴，说明 stage 轴过于相关，升级第二层轴：**workload regimes**（能量区间 / 事件类型）。

轴的纪律：

- 只使用 reality-defined 的 measured axes——diversity 来自实际 evaluation，不是 Harness 猜什么叫 diversity；
- correctness / gate 不是轴（gate = validity，不是 niche）；
- 人为 optimization category 不是轴；
- per-event runtime 不是轴（噪声会制造垃圾 frontier）。

### Executor 侧的配套

Frontier 只控制 proposer 注意力；Executor 侧的规则见 §12（有界队列 + 机械 backpressure）。

---

# 8. Scientist Continuity

当某个 Scientist 在 Parent Node 上提出 Proposal，并由该 Proposal 产生 Child Node 时，Child Node 可以继承该 Scientist Thread。

例如：

```text
T7 @ N3
↓ P18
↓ Experiment
N8
↓
T7 continues @ N8
```

它能够看到：

- 自己为什么提出 P18
- 当时 expectation 是什么
- 真实结果是什么
- 新世界与旧世界有什么变化

从而形成：

`Judgment → Experiment → Reality → Belief Revision`

如果一个 Scientist 的多个 Proposal 都产生 Child：

```text
          T7 @ N3
         /       \
       N8         N9
      T7a         T7b
```

Scientist cognition 从 Proposal 提交时的 snapshot 分叉。

因此每个 Proposal 都应对应明确的 Scientist cognitive snapshot，确保 Child Scientist lineage 在异步完成和 Resume 后仍然确定。

Snapshot 与实验结果的拼接点是固定的：**snapshot 冻结在 Proposal 提交时刻，事后不被修改**；Experiment 结果不写入 snapshot，而是由 Scheduler 在下一次 Proposer Job 启动时，从 L2 事实组装 world transition 记录（parent → child 的 metrics / gate / diff），与冻结的 L3 快照一起作为 Job 输入。认知保持"提交时的我"，现实由 Harness 在启动时呈现，两者在 prompt 层相遇。

不同 Scientist Thread 之间的信息流动只有一条通道：

> **认知不共享，现实共享。**

一个 Scientist 可以通过 L2 查询其它 branch 的实验事实（metrics、gate、diff、结果），并据此修正自己的认知；但任何 Scientist 都不能读取其它 Thread 的 L3（notebook、beliefs、session）。跨 lineage 的信息一旦进入某个 Thread，必须经由它自己的判断写入它自己的 L3。否则 fork 产生的认知多样性会重新坍缩成 distributed shared-brain，Tree diversity 也就失去了根基。

---

# 9. Proposer Workspace

Proposer 被分配到某个 Node 后，应拥有一个明确隔离的 Scientist Workspace。

其信息可分为三个部分。

### Node World

客观权威世界：

- Exact Node SHA
- Task artifacts
- Gate / evaluation definition
- Current metrics
- Source repository

Scientist 必须研究自己的 Node。

不能自动跳转 global best，也不能自动 merge 其它 branch，否则 Tree 的因果语义会被破坏。

---

### Scientist Lab

Scientist 自己的认知空间：

- Scratch
- Notes
- Investigation artifacts
- Notebook
- Session state

不同 Scientist 即使研究同一个 Node，也不共享认知空间。

因此：

> 同一个 Node 上的 Scientist 生活在相同客观世界，但不同认知世界。

---

### Research History Interface

历史不应默认完整注入 Workspace。

Scientist 按需查询 L2，并在必要时深入 L1。

---

# 10. Proposer 可见信息

Proposer 是 **local intelligence**。

它主要沉浸在自己当前的 Node 上。

默认 context 应包括：

### 固定 Context

- Scientist Charter
- Goal
- Current Node identity
- Current Node metrics（含各 measured axes）
- Current world transition

### Node World

- Exact source SHA
- Repository
- Task environment
- Gate definition

### L3

如果是 continuity Scientist：

- Notebook
- Current understanding
- Beliefs
- Expectations
- Important unknowns
- Parent → Child experiment result

### 按需查询的历史

Proposer 默认不需要看到整个 Research Tree。

尤其不应默认看到：

- 其它 Scientist 当前 pending Proposal
- 全局热点 branch

否则容易造成认知热点坍缩。

Proposer 主要应该知道：

> 现实已经证明过什么。

而不是：

> 其它 Scientist 正准备做什么。

---

# 11. Proposer Tools

Proposer 需要两类能力。

### Local Investigation

围绕当前 Node：

- Shell
- File read
- Code search
- Git inspection
- Scratch analysis

这些用于真正理解当前 world。

### Research History Query

围绕 L2：

- Inspect Node
- Inspect Experiment
- Inspect lineage
- Search experiments
- Compare Nodes
- Search historical research facts

L2 信息不足时，可以进一步 drill-down 到 L1 full trace。

因此：

`L2 = index`

`L1 = raw evidence`

---

# 12. Proposal → Executor 队列语义

Proposal 无需审查，直接进入 Executor Queue。Proposal 状态：

```text
queued → running → done
        ↘ overflowed-dormant（队列溢出，或 parent 已离开 Frontier）
```

全部规则都是机械的：

- 队列有界，FIFO；
- 溢出 → overflowed-dormant（不丢失，可复活）；
- parent 已离开 Frontier 的 queued proposal，由 Scheduler 机械清理为 dormant；
- Executor slot 空出时取队头，不触发任何判断；
- Executor 空闲不是系统问题，是研究供给不足的真实信号，不需要凑满 pool。

Executor Queue 因此是 proposer 产出速率和 executor 消费速率之间的解耦层。

---

# 13. HTCondor Execution Model

Proposer 和 Experiment 应作为两类独立 HTCondor Job。

Proposer Job 运行 Scientist runtime 的常规科研模式；Experiment Job 运行独立的 coding agent（`claude -p`）完成 Executor → Eval → Gate。

---

## Proposer Job

输入：

- Node identity
- Node SHA
- Scientist Thread state
- Goal
- Workspace definition
- History query capability

输出：

- Proposals
- Scientist updated snapshot
- Expectations
- Research metadata
- Full L1 trace

---

## Experiment Job

输入：

- Proposal identity
- Parent Node
- Parent SHA
- Proposal content

内部对应：

`Executor → Implementation → Commit → Eval → Gate`

输出：

- Result SHA
- Metrics（含全部 measured axes）
- Gate results
- Experiment facts
- Full L1 trace

MVP 中 Executor / Eval / Gate 属于一个逻辑 Experiment Job。

---

# 14. Resource Pools

Proposer Pool 和 Executor Pool 表示的是**逻辑并发配额**，不是长期常驻进程。

例如：

```text
max_proposer_inflight = P
max_experiment_inflight = E
```

Evolution Scheduler 决定：

> 哪个 research work 获得计算资源。

HTCondor 决定：

> 这个 Job 在哪台机器、什么时候执行。

两者职责完全分离。

---

# 15. Resume 是基本系统语义

长期 Evolution 必须假设：

- API 余额不足
- 网络中断
- Harness crash
- Worker lost
- Condor evict
- Scheduler restart

随时都会发生。

因此 Resume 不是附加功能，而是系统基本语义。

核心原则：

> Harness 进程可以消失，但 Research State 不能消失。

---

# 16. Logical Work 与 Execution Attempt

所有长期科研工作都有稳定 identity。

例如：

- Proposer Assignment
- Experiment
- Scientist Thread

实际 HTCondor execution 只是 Attempt。

例如：

```text
Experiment E91
├── Attempt 1 → network failure
├── Attempt 2 → Condor evicted
└── Attempt 3 → success
```

三个 Attempt 仍然属于同一个 Experiment。

---

# 17. Infrastructure Failure 与 Scientific Failure

必须严格分开。

以下属于 infrastructure failure：

- API failure
- Network timeout
- Condor eviction
- Machine loss
- Temporary filesystem failure

这些不能变成：

> hypothesis failed

它们只意味着 Experiment 尚未完成，可以重新产生 Attempt。

而：

- 正常实现完成但 Gate fail
- Metric 变差
- Proposal 没有产生预期效果

属于真正的 scientific result。

即使结果不好，Experiment 也应标记完成，而不是无限 retry。

---

# 18. Resume Reconciliation

Scheduler 重启后首先进入恢复状态，而不是直接继续提交新 Job。

它基于 SQLite 找出所有 non-terminal work，并与真实 HTCondor 状态以及 L1 durable artifacts 对齐。

可能存在：

### Job 仍在 Queue / Running

重新 attach，不重复提交。

### Job 已完成但结果尚未写入 SQLite

读取 durable result，完成 Research DB commit。

### Job 确认丢失或 infrastructure failed

在相同 logical work 下创建新的 Attempt。

Resume 的目标是：

> 恢复研究状态，而不是恢复旧 Python process。

---

# 19. Proposer 与 Executor 的 Resume 语义不同

### Proposer

必须恢复原 Scientist Thread。

它保留：

- L3 cognition
- Notebook
- 已完成 tool interactions
- Current Node
- Workspace identity
- Full L1 trace

因此 API session 消失后，仍然可以语义上恢复“同一个 Scientist”。

---

### Executor

Executor 不需要认知连续性。

如果实现过程中 infrastructure failure，可以：

- 保留 Experiment identity；
- 新建 Attempt；
- 从相同 Parent SHA 和 Proposal 重新开始。

也就是：

> Experiment continuity 保留，Executor process continuity 不要求。

---

# 20. Atomic Research State Transitions

所有会改变 Research Tree 的关键状态都应具有明确的语义原子性。

### Proposal Publication

Scientist 完整形成 Proposals 后，它们才成为 L2 Research State。

未完成的 Agent 输出仅属于 L1 trace。

---

### Experiment Result → Child Node

Experiment completion 和 Child Node creation 属于同一个逻辑事实：

```text
Experiment completed
+
Result SHA
+
Metrics
+
Gate
+
Child Node
```

避免同一个 Experiment 在 Resume 后生成重复 Child。

Frontier 重算发生在同一个 ingest 事务内：Node 落库与 axis-winner 状态更新是原子的。

---

# 21. 终止语义

演化没有 round，因此没有 max_rounds。系统只识别三种终止：

## Budget exhaustion

token / experiment / wall-clock 预算耗尽。预算是 Harness 的外部资源约束，不是科学判断。

## External stop

人工停止。

## Quiescence

Executor Queue 空、无 running jobs、Frontier 中所有 Node 都已获得研究且不再产出新 Proposal——当前不存在任何能继续推动演化的动作。

Quiescence 是**可唤醒的自然静止**，不是"研究完成"的终局宣判：静止后不销毁任何状态，人工 reseed 或新事实注入都可以唤醒系统。系统不设置终止 Judge。

注意一个刻意保留的不对称：Frontier Node 的"枯竭"是当前 cognition 的主观枯竭（continuity Scientist 不再产出 Proposal），不代表换一个 fresh cognition 也枯竭。是否为非 Root Node 引入"认知续种"机制，见 §24 Parking Lot。

---

# 22. 整体架构

```text
                    L1 Full-Fidelity Trace
                   /                      \
             Proposer                    Executor
                   \                      /
                    L2 SQLite Research DB
                             │
                  Research Tree（Archive = 全部 valid Nodes）
                             │
                  Frontier Selection（机械计算）
                  union of per-axis winners
                             │
                      Proposer capacity
                             │
                        Proposer Job
                             │
                         Proposals
                             │
                  Executor Queue（有界，机械规则）
                             │
                      Executor capacity
                             │
                        Experiment Job
                      Executor → Eval → Gate
                             │
                          New Node
                             │
                        └──→ Archive


             L3 Scientist Thread State
                       │
                       └── follows Parent → Child cognitive lineage
```

---

# 23. 最终设计定位

这套系统不是“并行版本的 SimpleLoop”。

它是一个：

> **以 Research Tree 为世界结构、以 SQLite 为结构化研究状态、以 L1 保存全真研究轨迹、以 L3 保存 Scientist 认知连续性，由异步 Evolution Scheduler 以 GEPA 式 measured-axes Frontier Selection 分配 Proposer 与 Experiment 资源，由局部 Scientist 负责探索具体 world、由 Executor/Eval/Gate 提供现实反馈，并能够在任意基础设施中断后恢复的持续 Research Evolution Harness。**

它的核心价值不在于把一次 Loop 放大，而在于允许：

- 多个 Research Worlds 同时存在；
- 多条 Scientist cognition lineage 同时发展；
- 新方向与有价值深入同时保留；
- 惯性 continuation 因无法赢得任何 measured axis 被 Frontier 结构性断粮——不需要全局判断者；
- 研究事实与认知判断严格分离；
- 整个演化过程能够长期运行、审计、可视化和恢复。

这构成新 Harness 的基本设计边界。

---

# 24. Parking Lot：已识别但暂不实施的机制

以下机制都在设计讨论中被认真提出过，但它们预防的是**尚未观察到的 failure mode**。SimpleEvolution 的设计纪律是：每个机制必须对应一个已观察到的真实失败——简洁不是什么都不能有，而是只做必要设计。因此它们先停在这里，各自标注复活条件：

| 机制 | 预防的问题 | 复活条件（观察到什么才实施） |
| --- | --- | --- |
| **Reviewer**（全局语义过滤角色：batch review、ADMIT / DEFER / DROP、reservoir） | Proposer flooding executor；惯性死磕在实验发生前被拦截 | L2 统计显示 executor 预算被重复 / 惯性实验显著消耗（例如同 mechanism family 连续 N 次无 metric 移动的实验占比超阈值）。其历史子机制（evidence_refs 硬协议、bias 缓解、遥测策略、预构造 global snapshot）随 Reviewer 复活一并重新评估 |
| **ε-exploration**（少量 proposer 容量随机分配给 Frontier 之外的有效叶子） | 所有轴都差一点但离突破一步之遥的 stepping-stone Node 被结构性饿死 | 连续 N 个 experiment 没有产生任何新 axis winner（Frontier 停滞），或 §7.2 的分配分布遥测显示单轴 winner 实际频率趋近于零 |
| **非 Root Node 的认知续种**（给被 continuity Scientist 判枯竭的 Frontier Node 派 fresh scientist） | 单一 cognition 假枯竭导致系统早停 quiescence | quiescence 发生后，人工 reseed 确实产出了新 axis winner |
| **Workload-regime 轴**（能量区间 / 事件类型作为第二层 Pareto 轴） | Stage 轴过于相关，Frontier 过窄 | \|Frontier\| 长期 ≈ 1~2，或单 lineage 长期占满所有轴（§7.2 遥测） |
| **两级 eval cascade**（cheap smoke eval → 通过才跑 full eval，机械） | Eval 成本吞噬预算 | Eval 时间 / 费用成为预算瓶颈 |
| **Proposal 机械 dedup 信号**（target-file 重叠、文本相似度） | 重复 Proposal 浪费 executor | 重复实验在 L2 统计中占显著比例 |
| **Branch merge / crossover**（GEPA merge：组合不同 branch 的互补改进） | 两个 branch 各自的 axis 优势无法组合 | 观察到两个 branch 分别稳定持有不同轴，且 Scientist 反复手工提出组合型 Proposal |

共同纪律：新机制进入主设计之前，必须能引用 L2 / L1 中的具体证据，说明它在修哪一个已经发生过的失败。
