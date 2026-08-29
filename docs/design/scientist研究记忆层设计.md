# scientist 研究记忆层设计（View / Research Memory / Evidence 三层）

2026-08-29 定稿。来源：R7 生产 run（jrb-full-std-elec-r7-scientist，test 2.03% 全臂
最优）暴露的认知持久化缺陷 → 与 HEP（Hypothesis Evolution Protocol，arXiv:2607.09195，
东大生研所）对照 → 三方讨论收敛（本会话 + docs/chat/2026.8.29.19.02.gpt谈researchstate的
缺陷.md 两轮往返）。本文档是实现前的定案：**实现未开工，批后动**。

---

## 1. 问题：R7 证据链

R7 赢了判分（能量轴首次压过 coding 臂），却输掉了顶点轴（59.7 vs 33.8cm），且输法
是系统性的。全部病灶都在 research state 上，各有 wire 级证据：

1. **洞见蒸发**。PI 早期亲测发现"发射 87% prompt，vertex/timing 可用 lead+TOF 做"
   （wire rec#30，早于任何席位）——这句话只存在于对话流，之后 60+ 步无任何记录
   回看，最终死在上下文深处。
2. **变体之死被记成父假说之死**。executor-001 报告原文带 scope（"timing
   triangulation 62 (radial-only)"，且天花板声明限定 "with charge-only centroid
   methods"）；跨席位边界吸收时限定词丢失，"radial-only 死了"变成"timing 死了"。
   根因：接收端（判词）是压缩器，蒸馏压强下边际信息先死。
3. **中途不可用**。能量到地板的里程碑（rec#133），PI 重读的是记分牌，选了 "safe
   lever"，pattern 通道挖到 50.4 贴自家天花板。关键细节：**临终 handover 的
   open_questions 里白纸黑字重新写出了 joint TOF+PE-count likelihood**——它不是
   不知道，是里程碑时刻够不着。病是中途 recall 无输入，不是"不会想"。
4. **state 是记分牌不是地图**。判词三版全是陈述句（Established/Solver now/at
   floor），evidence_refs 三版全空；notes.md 写了一篇即弃且内容含后来被证伪的论断
   （"pe/e flat across radius"）；dead_ends/open_questions 临终才出生。
5. ** proposer 空索引盲开局**。experiments.jsonl 是预置只读语料，本部署未接记录
   机制，neutral evidence index 恒空。

结构根因一句话：**research state 只有一张可覆盖的纸（judgment，`_upsert_judgment_message`
原位替换），它被迫同时兼任"当前注意力"与"全部长期研究记忆"两个职责——前者它干得
很好（3/3 修订都在真节点），后者结构性不可能。**

## 2. HEP 借什么、不借什么

借（经 R7 证据逐条对上）：
- **重要认识一旦形成即获得持久身份**（persistent object），不因注意力转移而消失；
- **append-only**：不压缩只追加，当前状态是投影——限定词在此结构上不可能丢失；
- **历史进入攻击面**：审查者可以攻击"你为什么放弃了 X"，而不只攻击当前信念。

不借（砍单，三方一致）：
- P(H) 数值与 0.8/0.2 阈值门（自报数字的伪精度；方向感够用）；
- 五态生命周期协议与合法迁移执法（协议执法与我们"环境不执法"原则冲突）；
- hash 链、独立注册表、merge/refine 算子（为百假说规模设计；wire 本就是事件溯源；
  谱系在我们这儿自然生长）；
- **自动重定价 / 修订时强制走查 parked 集**——见 §6，这一刀的代价与理由。

## 3. 设计原则（新入册，与既有两原则并列）

既有：harness 只提供环境不插手内部决断（可强制知情程序，不评判决断内容）；
agent 不是婴儿（信息自取，不靠 harness 喂饭）。本轮新增三条：

- **P3 给工具不给操作系统**：不要替 Scientist 组织搜索空间；给它一个能自己组织、
  保存、检索搜索空间的工具。
- **P4 记忆指给看，不喂**：协作者访问的是 Scientist 的研究环境，不是 Scientist 为
  它准备好的 context package。Harness 保证信息可得、位置明确、权限正确；看什么、
  看多少、看不看，由有能力的 agent 自己决定（v6 对 open proposer 的偏移隔离
  （"You have intentionally not received the Scientist's current preference"）是已
  验证的反锚定设计，喂图会部分拆掉它——锚定是已知风险不是猜想）。
- **P5 选步是判断，不是机制**：记忆永不自动排序、永不替它挑门。门的选择机械化
  = 在 scientist 肚子里重建进化层（supervisor/evolution 是更高层级架构，明确不混入）。

措辞纪律（KILL_KNOCK 先例：义务式文本让行为更糟）：**工具描述只给事实性说明
（是什么、持久、怎么搜），不给程序性指令（何时必须扫、必须记）。**

## 4. 三层架构

```
                Scientist（单 agent，本体工作 = 维护搜索空间）
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
 Current Research    Research Memory     Seats（机制全套不动）
 View（注意力）       （记忆，新增）       executor/proposer/
   一页自由文本         persistent items    challenger/reviewer/searcher
   可覆盖               append-only
        │                 │
        └──────── Evidence / World（事实层，现状不动，只被引用）──────┘
              席位报告 / 实验记录 / 代码与 git / wire / 文献
```

### 4.1 View：现有 judgment 的重释，不是简化

judgment 原封不动变成 view：触发时机不变（真实里程碑）、自由文本不变、一页不变、
`_upsert_judgment_message` 原位换入上下文不变（view 本来就该换）。R7 已证明这个
器官健康。它唯一卸掉的职责：不再负责保存所有未来可能有用的历史认知——交给
Memory。**这是一个器官兼任两职责的拆分，不是削弱。** view 的内容回到纯注意力：
"我现在怎样理解这个问题，此刻哪些东西值得我关注"。**View 不承担同步描述当前
实现的义务**——代码是自己的 source of truth，与 docstring 失同步是"承担了该义务"
的实证（R7 判词 50.4 vs docstring 53.0）；但 R7 也证明构成描述常常是 brief 的原料
（executor-005 任务书整段引用判词物理）。所以只保留 Scientist 判断对当前科研
决策有用的世界抽象——写什么，PI 定。

### 4.2 Research Memory：可关联的研究记忆，不是 formal research graph

item 最小形态（"一个 ID + 一段 Scientist 亲笔的意思"）：

```
R17
content:   Timing may contain vertex information beyond radial correction.
status:    parked
evidence_refs: [executor-001]
note:      Only radial formulation has been tested.
```

- **status 三态**：active / parked（带理由）/ closed。语义只是"我要不要继续关注它"，
  不教科研。
- **close 必须带 scope**——唯一字段级硬约定（与席位七字段 fence 同级的软契约）。
  理由：R7 的丢失发生在压缩面上；memory 虽无压缩压强，但**字段让缺席可见**
  （content 里没写 scope 看不出漏了，字段空着一眼可见，challenger 攻击面可及）。
- **kind（finding/question/idea）与关系（related/derived_from/tested_by/supersedes）
  降为可选描述能力**：想用就用，允许 `R12 --related--> R17` 也允许只是一句自然语言
  note。我们要解决的是**信息丢失**，不是"关系没有机器可解析"。TOA 三重损失
  （蒸发/限定词/中途不可用）分别由 identity+remember、close-scope、search 解决——
  **formal lineage 不是承重墙**：只要两个对象没有被压成一句话，它们就不会一起死。
- **evidence 只挂引用**（席位 call_id / 实验 id / 文件路径），不复制内容。
- **创建纪律从宽（判断"未来可能需要重新想起"才记，无逐念登记义务），驱逐纪律
  为零（只 park 永不删）**。
- 存储：`research_memory.jsonl`（append-only，落在 `.scientist/`）。状态变更即追加
  事件，历史天然保留。

### 4.3 Evidence / World：完全不动

wire、bash 流水、bench 记录、席位完整报告、源码、git、文献——各有 source of
truth。memory 是 semantic research memory，不是 episodic log：**不把历史图化，
否则造出另一个 SQLite 日志系统**。

## 5. 动作面（改动集中在 judgment 一族）

- `remember`（新增，廉价随手记）：记 item / 改 status / 挂 evidence ref，一个动作，
  不打断主线。记边栏必须比写作文便宜，否则没人用——这是我们现在没有的科研工具。
- `revise_research_state`（`revise_research_judgment` 语义升级）：仍是里程碑科研判断
  行为，一次做两件事——重写 view（现状不变）+ 顺手维护 memory（create/park/close
  带证据）。**不含**强制走查（见 §6）。
- 检索三件套：`search_research_memory` / `list_research_memory` /
  `inspect_research_item`。
- 工具描述措辞按 §3 纪律：只述事实（"这是你自己的长期研究记忆，跨整个 run 持久，
  随时可检索"），零程序性指令。

## 6. Resurface：架构承认，v1 不装

R7 的双证据同时成立：PI **有能力**重新想到旧方向（临终 handover 重写出了 joint
TOF+PE open question），却在真正需要它的中途里程碑**没有想到**（rec#133 选了
safe lever）。所以问题不只 storage failure，还有明确的 **attention / recency
failure**：

> Memory 存在 ≠ Memory 会在正确时刻进入注意力。

据此分两层：

- **架构层（承认问题在范围内）**：Persist → Research Memory ← Retrieve 之外，
  预留 Resurface/awareness 环节，其原则现在定死——harness 最多提醒 Scientist
  "**你的长期研究记忆存在，当前格局变化后它可能值得重新查阅**"；**绝不**告诉它
  该看哪条、哪条重新升值、下一步做什么。若启用，触发必须稀疏、内容无关、零指令
  动词（预算注/听证门同族：内容无关的知情提醒，非判断）。
- **产品层（v1 先测纯检索）**：不装任何走查/提示/awareness cue。理由：①强制扫
  parked 集是替 Scientist 设计科研 workflow；②预装习惯会污染下一个 run 的实验——
  永远不知道裸检索够不够（KILL_KNOCK 先例：义务式文本让行为更糟）。

代价（明写）：本设计最大的预期收益——里程碑时刻 parked 洞见回到桌上——在 v1 里
是**行为假设**而非机制保证。两条补偿，均有实证：①R7 的 PI 在里程碑本来就主动问
reviewer "what am I missing"（step 59 汇报词原文），求助反射在场；②challenger 带
memory 后成为第二次召回机会（"你的 view 暗示 timing 无价值，但 R23 只测了 radial，
R17 还站着"是真 challenger 的本职，不是 harness 在 challenge）。验收指标②不过时，
开启的是 §6 已定义的最后一环，不是临时发明新机制。

## 7. 席位接口：指针 + 事实句

- memory 文件随**所有**席位的 fork 出海（`_fork_world` 增加 memory 文件的 include，
  精确到文件，不是整个 `.scientist`）。分界线：**研究记忆是公共知识层，随 fork
  走；wire 是私人思路流，不出海。** 反锚定意图因此保留（不灌原始推理），公共知识
  成为共享基建。
- 各角色 mandate 加一句**事实句**（非义务句）："本 run 的研究记忆在 `<path>`，含
  Scientist 已形成的重要认识、探索历史与证据引用，可在需要时查阅。"看多少、看不
  看是席位的专业裁量：窄任务 executor 可以不翻；challenger 不查旧判断大概率没尽到
  职责；de-novo proposer 可以只查 dead history 做查重、故意避开 current framing。
- **不给摘要、不预选节点、不替它读。**（P4）
- canonical memory 只由 Scientist 写；席位报告是 evidence ref，不直接改 PI 的
  长期认知——Scientist 对研究判断负责，帮手是杠杆。
- **conclusion 查阅、引用、吸收 Memory，但不被 Memory 定义**——Memory 是输入，
  不是 source of truth。memory 是选择性语义记忆，必有临终才成形、此前未 remember
  的认识；机械导出会把 R7 已证明很强的临终诚实总结（6 死 4 开全带证据）降格为
  库存清单，且本质是 harness 替 PI 写结题（违 P3）。最终科研总结始终由 Scientist
  自己做。

## 8. R7 全程推演（新设计下的六幕，机制在场性检查已过）

1. **亲探期**：行为不变。rec#30 时刻顺手 `remember R1(active)`——接住的是它本来
   就会说出口的话，纯增益。
2. **通道路由**："图案→vertex、时间→timing"从隐形架构变成可见的 parked item
   （理由：质心路线立即可用，先时间轴）。
3. **首派吸收**：executor-001 报告回来，close R23 **带 scope="radial formulation
   only"**、evidence=executor-001；R17 不动。限定词有了指定落点。challenger 径向
   发现 supersede 旧"pe/e 平坦"条目。
4. **中途听证**：reviewer 照旧自由翻；新增可审计面（"R23 写着 radial-only，为何
   timing-direction 从未测过？"）。
5. **岔口**：能量关、vertex 唯一轴。`search_research_memory("vertex")` → R17 回到
   桌上（行为假设，验收指标②，见 §9）。
6. **收笔**：conclusion 从 memory 导出。

不减弱清单（每项 R7 实证）：里程碑修订纪律 3/3、亲探先行、批判性吸收（推翻过
reviewer 地板）、席位 7/7 高质量 fence、临终诚实——全部无变化或只会更稳。

## 9. 验收指标（下一 run 可测）

1. **memory 使用率**：记不记（notes.md 写一篇即弃是前科）；
2. **里程碑 recall 行为**：里程碑/意外/饱和时刻用不用检索三件套。

两条全过 → 设计成立；第②条不过（memory 在、从不翻）→ 开启 §6 已定义的
Resurface awareness cue（内容无关的存在提醒），而非新发明机制。建议先跑一次
live 探针（一个上下文、一次调用，复刻 R7 场景给 PI 用新工具面）再上真 run。

## 10. 实现面预览（批后动）

- `scientist/ledger.py`：research_memory.jsonl 读写 + item/事件模型 + 检索三件套；
- `scientist/native_tools.py`：remember / revise_research_state / 检索工具注册与
  事实性描述；
- `scientist/agent.py`：`_judgment_message` 改名/重释为 view；dispatch 接新动作；
- `scientist/assistant_tools.py`：`_fork_world` 增加 memory 文件 include；
- `scientist/collaboration.py`：各角色 mandate 加事实句；challenger 的
  "Judgment to attack" 不变（view 即其对象）；
- conclusion 走查阅/吸收语义，无导出路径；既有自测模式照第四轮七组先例扩组；
- **v1 不实现** Resurface cue（§6 留位，验收②不过才启用）。

## 附：讨论 provenance

- 本会话：R7 失败分析（TOA 三重损失、记分牌诊断、五幕→六幕推演、四格→自由文本
  view 的让步、lineage 降级、砍重定价扫描）；
- docs/chat/2026.8.29.19.02.gpt谈researchstate的缺陷.md：提案 agent 三轮
  （第一轮三层架构与五类对象；第二轮 item 最小形态、persistent identity 解决
  关键一半、能力先行习惯后置；第三轮终审——conclusion 是 Memory 的输入非定义、
  Resurface 架构留位 + 内容无关提醒原则、View 不承担同步描述义务）；
- HEP：arXiv:2607.09195（persistent hypothesis + append-only 事件史的价值证明）。
