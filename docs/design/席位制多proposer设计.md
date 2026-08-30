# 席位制多 Proposer 设计（透镜席位 / Seat-based Proposers）

状态：**已实现（v6）· 第八轮 v7 设计定案（并行与续用，见文末）· smoke 首跑验收通过（见 §8）** · 实现于 2026-08-23
实现记录见 §9；验收脚本 `scripts/check_seat_v6.py`，smoke run
`runs/seat-v6-smoke`（examples/xsbench_opt/task-supervisor-v6-smoke.yaml）。
证据来源：runs/ablation-v5（旧架构验证 run，反面教材：树臂 1.66h 以 2 evals/
$1.30 quiesce 自杀——单冠军收割链的终局）、runs/supervisor-tree-xsbench-v3、
runs/ablation-v4
配套工件：`generator.json`（透镜基 v2 三件套，生产基）、
`scripts/_lenses_v2.json`（探针用副本）、`scripts/_probe_lens_seats.py` +
`scripts/_probe_lens_results.json`（探针与原文）

---

## 0. 一句话

把"多样性"从 agent 的禀赋改成**编制**：一个节点不再由一个负全责的 Scientist
研究，而是由 N 个**席位**研究——每个席位戴一枚**生成元（透镜）**、只负责把
自己学派的那一问问好。广度是编制给的，深度是每个席位的本职，责任心恰好一问
之重。

## 1. 为什么改：v5 的诊断链

当前单 proposer 架构在 v5 验证 run（4h 时间帽、预算不限）中的完整病理：

1. **单冠军收割**。每个研究只提交锦标赛冠军：v5 前两批研究各自的 working
   model 都列出了 3-4 个候选机制，但 lab 实测后只提交 1 条（第一批甚至把两个
   独立已验证机制**捆绑**成一条 proposal，树宽在免费处饿死）。v3 统计：30 个
   研究的 proposal 分布 = 0:11（37% 弃权）/ 1:9 / 2:8 / 3:2，均值 1.03，slots=4
   从未打满。
2. **通道定价失衡**。多测一个 lab 变体 ≈ 4 步；多提一条 proposal ≈ 250 词结构
   化举证（instruction + expectation + research_target 四件套 +
   material_difference + evidence_refs）。低 effort 模型理性地把多余想法全部送
   进便宜的 lab 通道，死在里面——广度被伪装成深度。v3 反证资源假说：出
   proposal 越多的研究步数越**少**（3 条/研究 ≈ 24 步 vs 1 条 ≈ 34 步；最深的
   64 步研究零产出弃权）。
3. **单链涌现闭环**。每研究 1 冠军 → 每节点 1 子 → supervisor 视野里"不存在
   竞争谱系" → 只能资助链尖 → 新研究又在链尖上做。两个角色各自理性，链是局
   部理性合谋的涌现物。
4. **1.66h 自杀**。第三个研究在活跃调查中弃权（abstain 理由"User requested
   to stop."系模型幻觉——session 里无任何停止指令，且 0 个 research state 注
   册，37 步调查全部蒸发）→ supervisor 读作"机制耗尽"，空 growth 决策 →
   quiescence → 程序在预算剩 96.5%（$998.7/1000、2.4h/4h）时自停。
5. **透镜机制空转**（v3）。生成元基 10 条真实存在、reseed 注入通路存在，但：
   4/26 节点用过第二席位；`select_one_generator` 按 basis 顺序取第一个 → 永远
   G1，G2–G10 从未出场；G1 是"建议"不是身份，其实际产出退化为程序内 donor
   移植（SYNTHESIZE 的本职）。**透镜在场，但既没有席位也没有权威。**

## 2. 核心结构

### 2.1 席位

- 席位 = **(节点 × 透镜) 的一次租约**。产出恰好两选一：**1 条 proposal**
  （explore 新方向，或 synthesize 合成 inspected donors——合成权保留在席位，
  因为 supervisor 不会被动读到 research state，自下而上的合成发现需要直接行
  动通道），或 **1 份空透镜备忘**（本视角在此世界确实无矿，须指名检验过什么、
  为何皆空——调查不蒸发）。
- **提交语义：数量不出现在 prompt 里。** 每席位恰好一条 proposal 由 harness
  机械实现（租约只保留一个 proposal_id），prompt 只用所有格单数表达——"维护
  好你对这个世界的理解，提交**你的** proposal"。不出现 slots、不出现"一条/每
  方向一个/多条中选优"等任何数量语言：数字会重新引入组合管理与择优的旧思维
  （旧 charter 的 slot 叙述正是这么养出来的），而席位制里没有组合可管——席位
  只有它的一问。
- 席位职责恰好一问。多席位并发于同一节点（现有 `max_proposer_inflight=6` 的
  并行租约机制天然支持）。
- 旧的 `max_research_per_node` 硬帽**溶解**：节点值不值得继续研究，由
  supervisor 的边际出价决定，预算事实是天然边界。

### 2.2 透镜（生成元）的地位与内容

**地位——prompt 三点锚定，对抗长上下文淹没：**

1. **System prompt 第一行**：席位身份（"You are the G5（反演）seat of node
   X. Your lens is your identity… not advice you may weigh."）。session
   compaction 不丢 system 层。
2. **负面禁令**随透镜正文（非 G10 席位禁止"profile→打最大数字"默认开局等）。
   负面约束对 LLM 的绑定力强于正面劝导。
3. **提交时刻再锚定**：submit 协议要求 proposal 陈述"本方向如何体现本席位透
   镜"+ 透镜自检（见下）。最后一轮指令权重最高。

现有 wake payload 里的 `suggested_operator_id` 字段**废除**（它暗示"这只是建
议"）。

**内容——三件套标准（反花架子）：**

每条透镜 = ①**操作指令**（这一问具体怎么问）+ ②**负面禁令**（本透镜禁止的
默认剧本）+ ③**提交自检**（可判定的独特性标准：换个透镜也能原样产出的
proposal 不是本席位的产出）。

v2 全文见 `scripts/_lenses_v2.json`。关键修订：
- **G1** 堵漏：移植源禁止来自本程序实验史（那是 synthesize 的职责）——v3 的
  G1 席位实际产出是 donor 移植，透镜名号在场、动作未被执行。
- **G10** 显式化为**对照透镜**：它就是模型默认剧本（v5 无透镜研究精确执行了
  它）；单独给它设席位等于买一注本来就会下的注。
- 透镜质量是**运行期测量**而非设计期赌注：每条透镜的子节点 gate 通过率/改进
  幅度、透镜间方向离散度、遵循率进账本；与无透镜基线无差别的透镜改写或除名。
  基从静态清单变成被证据评级的演化工具箱。

**透镜分派**：全树随机/轮换（`sample_generators`），**谱系查重**——父链已烧
过的透镜，子节点再派需指名差异（G10 在父子节点会产出近似方向：同一个热点函
数摆在那里，重复分派 = 重复下注）。

### 2.3 继承

- 子节点通过 `originating_research_state` 指针继承**作者席位**的备忘（现状机
  制不变）：P1(G5) 的 proposal → 子节点，子节点的研究读 P1 的备忘；P2 的子节
  点读 P2 的。每条分支带着自己作者的世界观往下走。
- 备忘**署名透镜**（"这是 G5 席位的工作备忘"）——子席位大概率是别的透镜，署
  名让"这是某一学派的视角、可整体折扣"显式化，反锚定从劝告变成结构。
- **兄弟知识不继承、也不该继承**：继承是谱系通道；跨分支走台账
  （`search_experiments` + synthesize），保持 pull 式。
- **透镜不继承**：透镜是席位的属性不是血脉的属性，子节点席位重新分派、谱系
  查重。

### 2.4 Supervisor：全权 + 全视

- 决策升级为三元：**(节点 × 席位数 × 透镜/合成)**。这是"多样性定价权"：热节
  点加一席 = exploit；冷节点首席 = explore；合成 = consolidate；透镜产出统计
  让出价有据（配反单调警示：赢家透镜被重复购买会收窄多样性，正是要对抗的先
  验——anti-anchor 在分配层的同构物）。
- **不做菜单**（定案）：菜单 = harness 喂饭，与"每次 gate 失灵的修法都是把世
  界照给它看而非替它咀嚼"的既有演化线矛盾（budget facts → capacity facts →
  rejection feedback 三连修全是加事实）。supervisor 失误首先补事实，不预支成
  喂饭设计。
- 配置：**事实完备**（席位/透镜账本可查：每节点买过什么席位、谱系烧过什么透
  镜、各透镜历史产出统计——是世界的状态不是决策的答案）+ **charter 纪律**
  （每笔分配点名没买什么、为什么）+ harness 只执法不做主（不变量校验：同谱系
  重复透镜须指名差异、容量/预算上限——合法性合同，同 reserved-proposal-ids
  先例）。
- 死锁闭合：席位菜单（事实上的未试集合）非空 ⇔ 还有可买的问题；"没什么可资
  助"只在透镜真耗尽时合法 = 真·程序完成。quiescence 从此有诚实的触发条件。

### 2.5 合并（定案：合成权保留在席位）

| 层级 | 执行者 | 触发 | 产物 |
| --- | --- | --- | --- |
| 分支内发散 | 透镜席位 | supervisor 买席位 | 子节点（新方向） |
| 分支间合成 | **席位自己的 synthesize 动作** | 席位调查中发现合成机会（自下而上） | 子节点（组合机制，照常过 gate/eval） |
| 主干整合 | integrator → epoch 晋升 | supervisor 开 integration request（自上而下，低频） | 新 epoch 根 |

被否决的中间方案：合成席位（supervisor 购买的第三类席位）。否决理由：合并权
完全上收 supervisor 托大，且 supervisor 不会被动读到 research state/备忘，自
下而上的合成发现没有行动通道。席位保留双提交动作后，"发散预算被挪用"的代价
由"席位本来就只出一问"兜底：一个席位用唯一的一问做了合成，是它对那一问的合
法回答。

经济学变化要认账：席位制下兄弟分支是常态，合并从例外变成高频操作。两个天然
限幅：frontier 宽度照旧裁剪；supervisor 席位购买可分批（先 2 席，有苗头再
补）。

## 3. 探针验证（先行，架构不动）

**协议**（`scripts/_probe_lens_seats.py`）：冻结 v5 study-2 的真实 Child-world
wake 上下文（自带强前代方向 = 反锚定测试条件），11 臂（10 透镜席 + 无透镜对
照）× 3 采样，生产模型通道（deepseek-v4-flash，reasoning_effort=low——分歧
必须在低 effort 下成立）。每臂在被允许用工具前陈述：QUESTION（本席位之问）/
FIRST_ACTIONS / WHY_THIS_ANGLE。

**判据**：
1. 跨臂分岔：QUESTION 的机制类别分布 vs 对照（查找/内存布局/算法范式/预计算
   搬移/批处理重排/对称统一/尺度/其他）；
2. 臂内一致：3 采样问的是同一类问题；
3. 遵循率：WHY_THIS_ANGLE 真名透镜；G5 真反演、G1 拒绝 donor 移植、非 G10
   避开价目表开局；
4. 除名线：某透镜方向分布与对照无显著差异 → 花架子，改写或除名。

**结果**（33/33 成功、0 错误；`scripts/_probe_lens_results.json`；本轮 compact
charter 仍含旧数量措辞，各臂一致故分岔读数有效，下轮起用所有格单数版）：

| 臂 | 开局之问的形态（3 采样） | 判定 |
| --- | --- | --- |
| control | 2/3 "哪个剩余热点最大"（默认剧本）+ 1 条位一致范围核查 | 基线如预期 |
| G1 移植 | 编译器域的 predication/rank-select、**stringology 的 wavelet-tree rank/select**、Hacker's Delight 无分支查找——三个真外部域，**零 donor 移植**（禁令生效） | ✅ 强 |
| G2 分解 | producer/consumer 数据流重切、按概率重排分区、两阶段 batch 拆分 | ✅ 强 |
| G3 极限 | 无限内存极限（每桶一格）、perfect hashing 极限、HINT_BITS 缓存交叉点+L3 溢出 | ✅ 强 |
| G4 对称 | **"每个核素网格都是 unionized 网格的子列——把所有二分统一为一次窗口查找"**（提升统一）；对数分桶修复尺度不变性破坏 | ✅ 最亮 |
| G5 反演 | "每次查找的搜索**何时根本不需要**"、"把正确性责任从查找期翻转给建格期"——3/3 反转默认方向 | ✅ 强 |
| G6 范式 | #2 表示转置真范式；#0 表示清单；#1 问题行偏枚举（WHY 行锚回表示变更） | ✅ 过，1 条弱（可加禁令："哪个组件主导"不许作开局——那是 G10 的问题） |
| G7 异常 | 放大**真实异常**：lab 预测 24% vs 实测 45%；memo 声称基线 1.45M vs 父节点实测 1.19M；pick_mat 回归与排序矛盾 | ✅ 强（且抓到了 memo 里的真实数据不一致） |
| G8 形式 | "两个成功 diff 共享同一变换——预计算粗结构+局部工作——把该形式推到未变的兄弟结构上" | ✅ 过 |
| G9 尺度 | O(N) 项对二分的交叉点、N vs M 哪个尺度变量主导、增长类分析 | ✅ 强 |
| G10 价目表 | 3/3 "现在账本最大单项是什么"——与 control#1 同模 | ✅ 按设计=对照，坐实降级 |

**结论**：透镜不是花架子。9 个实质透镜的 27 条采样里，只有 1 条以默认剧本
开局（对照臂 2/3 默认剧本）；各臂在 WHY_THIS_ANGLE 里普遍正确命名自己的透
镜语义。目标区域收敛（多数臂都指向 per-nuclide 插值这一明显下个热点）而
**机制/角度发散**（统一、反演、移植、重切、极限、审计、形式迁移、增长类
——同一场地、不同学派的问法），正是议会想要的东西。**席位制获探针放
行**；G6 加一条开局禁令后可全量上场。

## 4. 实现面（最小 diff，探针达标后动工）

| 位置 | 改动 |
| --- | --- |
| `simpleevo/scheduler/loop.py` | 同节点多租约；席位分派（透镜随机/轮换 + 谱系查重）；`episodes.variation_operator` 复用为席位透镜字段（首席位也派透镜，不再只在 reseed） |
| `simpleevo/generator.py` | 分派改 `sample_generators`（全局随机），废除 first-untried 永远 G1 的行为 |
| `proposer/scientist.py` | seat system prompt 结构（透镜身份第一行 + 三件套 + 减薄 charter + 提交再锚定）；wake payload 的 `suggested_operator_id` 废除；`build_generator_catalog`（system hints 透镜目录）删除 |
| `proposer/prompts/proposer.md` | 拆出减薄席位 charter（责任从 goal 收窄到 question；认知纪律保留）；**slot 叙述三段整删**（unused-slots 段、"Prefer breadth…Submit every materially distinct"段、"each distinct direction gets its own proposal slot"段）；`transform_worldview` 描述段删除 |
| `proposer/cognitive_transformer.py` + `research_tools.py` + `context.py` | `transform_worldview` 整条通路删除（导师调用、action handler、operator nudge）——建议级透镜基础设施与身份级透镜矛盾；`cognitive_transformations` 表 dormant（保 schema 停写） |
| `proposer/prompts/supervisor.md` | 重写：决策动作从 `node_ids` 改为席位购买（节点 × 透镜 × 数量）；空选择语义改"未试席位耗尽才是完成"；charter 纪律（点名没买什么） |
| `proposer/prompts/integrator.md` | 集成备忘给 donor 署名来源透镜谱系（合并层反锚定提示） |
| supervisor wake facts | 席位/透镜账本 + 透镜产出统计 + 谱系透镜史（facts，非菜单）；`runtime_facts.proposal_slots` 换席位语义 |
| `simpleevo/db/` | **无 schema 变更**（`research_operation`/`donor_experiment_ids` 校验已在；`variation_operator` 已在） |
| 配置 | `max_research_per_node`、`max_proposals_per_node` 语义溶解；席位数上限由 supervisor 预算出价决定 |
| 不动 | `research_skills/reframe_inherited_problem.md`（反锚定方法，席位制下更关键：跨学派继承的高频解药）；`self_review.md`/`reflection.md`（无 slot 语言，职责仍成立）；lab 工具（grounding 单一方向仍是本职） |

## 5. 风险与开放问题

- **透镜权威上限是 prompt 级**。身份注入 + 负面禁令 + 提交再锚定 + 事后可检
  测（方向与透镜语义不符 = 席位失职）已是不换模型能达到的上限；接受"席位失
  职率"作为指标存在。
- **透镜全树查重**是硬需求，否则 N 席位只是把单链的重复从纵向改成横向。
- **supervisor 决策质量**：事实完备后若仍只买链尖，先分清"看不见"（补事实）
  还是"不想看"（charter/身份问题）。
- 探针是单呼叫开局分岔测试（Stage A）；通过后可再做全 session 版（Stage B，
  真实工具 + 多轮）。

## 6. 决策记录（含被否决方案）

| 决策 | 结论 | 否决的替代方案与理由 |
| --- | --- | --- |
| 多样性来源 | 编制（席位 × 透镜） | 单席位多 slot 收割：disposition-based breadth 在低 effort 下实证失败（1.03 条/研究） |
| slot 层透镜枚举 | 否决 | 被席位制整体取代——单席位内枚举仍是"一张图内的多样性" |
| 合成权 | 保留在席位（explore + synthesize 双动作） | 合成席位/全收 supervisor：托大，且 memo 无被动通道，自下而上发现缺行动出口 |
| supervisor 决策形态 | 全权 + 全视（facts 完备 + charter 纪律） | 候选菜单/勾选：harness 喂饭，逆"加事实不咀嚼"的演化线；实测决策 #4 是事实失败非推理失败 |
| G10 | 显式对照透镜 | 保留常规席位：它是默认先验，设席位于事无补 |
| 透镜内容 | 三件套标准 + 运行期评级 | 措辞依赖：无自检的透镜会被默认剧本同化（v3 G1 实证） |
| prompt 数量语言 | 所有格单数（"你的 proposal"），数量零出现 | slots/数量表述：重新引入组合管理与择优的旧思维；数量由 harness 保留单一 proposal_id 机械实现 |

## 7. 实现规格（紧凑交接）

### 7.1 Supervisor 决策动作（v6 schema）

```json
{"action": "submit_growth_decision",
 "seat_purchases": [{"node_id": "...", "lens": "G5"},
                    {"node_id": "...", "lens": "G2"}],
 "rationale": "... 必须点名一笔没买的席位（not-bought alternative）及理由 ..."}
```

- `seat_purchases` 为空 ⇔ 等待在飞证据；仅当 facts 显示**未试席位集合为空**
  （所有 (节点, 谱系未烧透镜) 组合耗尽）时才是程序完成。旧 `node_ids` 字段废
  除。`submit_integration_request` 不变。
- 调度侧：每个 purchase = 一个租约，`proposal_slots` 固定 1（保留恰好一个
  proposal_id——数量唯一性由 harness 机械实现），episode 的
  `variation_operator` 写入 purchase 的 lens（**首席位也写**，不再只在 reseed）。

### 7.2 透镜分派与谱系查重

- lens 来自 supervisor 的 purchase（不是调度器 first-untried）；调度器只**校验
  合法性**：该 lens 未在此节点的**祖先路径**上烧过，否则拒绝该 purchase 并回
  错误给 supervisor（rejection feedback 既有机制）。全局随机化仅发生在
  supervisor 请求建议时（若需要）。
- 谱系透镜史 = 沿 parent 链收集所有 episode 的 variation_operator。

### 7.3 席位 system prompt 模板（实现起点）

```
[第 1 行·身份] You are the {lens.id}（{lens.name_zh}）seat of node {node_id}.
Your lens is your identity: it is the angle you were hired for, not advice
you may weigh.
[第 2 块·透镜三件套] 操作指令 / 禁令 / 提交自检   ← scripts/_lenses_v2.json
[第 3 块·减薄席位 charter]
  - 职责：investigate from your angle, maintain your understanding of this
    world, and submit your proposal — exploring a new direction, or
    synthesizing inspected donors — or an empty-seat memo if your angle
    provably has nothing here. You do not own the whole problem; you own
    your question, asked well.
  - 认知纪律（保留自旧 charter）：belief 与 verified 分立；harness 裁决；
    your proposal needs a reason it deserves an answer, not a proof; do not
    pad it with a second mechanism; 前代备忘=署名学派的 memo 可整体折扣
    （reframe skill 就是为此存在）；lab 用于 grounding 你的问题，实验是
    program 的裁决。
  - 空席位备忘契约：注册你的 research state（调查不蒸发），并指名你沿透镜
    的哪些轴检验过、为何皆空。
[第 4 块·提交再锚定] submit 协议要求 proposal 附一句"本方向如何体现本席位
透镜"（透镜自检在此执行）。
```

硬性禁令：全文不出现 proposal 数量（slots/一条/N 中选优）；不出现透镜目录
（`build_generator_catalog` 删除）；wake payload 无 `suggested_operator_id`。

### 7.4 Supervisor facts（新增三块，wake batch）

```
seat_ledger:   [{node, seats: [{lens, outcome}]}, ...]        # 每节点已买席位与结局
untried:       [{node, lenses: [G3, G5, ...]}, ...]           # 谱系查重后的未试透镜
lens_stats:    [{lens, proposals, gate_pass_rate, best_gain}, ...]  # 全程序透镜产出统计
```

既有 facts（budget/capacity/质量事实）不变；`runtime_facts.proposal_slots`
移除，换 `seats_inflight` 语义。

### 7.5 席位 wake 上下文（首轮 user turn）

= 现有 Child-world 事实块（节点/实验结果/前代 proposal）+ **前代备忘署名透镜**
（"predecessor memo — filed by the {lens} seat；attributed view of one school,
discountable as a whole"）。变更：删除 operator 建议；其余不动。

### 7.6 提交路径

- `publish_research_batch` 不变（research_operation/donors 校验已在）；席位
  reserved 池恰好 1 个 id，第二条 proposal 天然被拒（"proposal_id not in
  reserved pool"）——单 proposal 不变量零新代码。
- 空席位退出 = abstain，但**前置校验：必须已注册 ≥1 个 research_state**，否则
  结果标记 invalid（修 v5 study-3 的蒸发式退场）。

## 8. v6 首跑验收单（smoke，目标成本 < $3）

1. 同一节点出现 ≥2 个席位、透镜互异（查 `episodes.variation_operator`）。
2. 席位 system prompt：透镜身份在第一行；全文 grep 不到 proposal 数量词；工
   具列表无 `transform_worldview`；payload 无 `suggested_operator_id`。
3. 每席位产出 ≤1 条 proposal（reserved 池拒绝第二条）或空备忘 + 已注册 state。
4. supervisor 决策解析为 `seat_purchases`；rationale 含 not-bought 指名。
5. 谱系查重生效：子节点席位透镜 ∉ 祖先路径透镜集；违规 purchase 被 rejection
   feedback 打回。
6. quiescence 仅在 untried facts 为空时触发（人为构造：透镜烧尽的节点）。
7. `reframe_inherited_problem` skill 仍可用；`cognitive_transformations` 表停写
   （dormant）。
8. smoke 后人工抽查：不同透镜席位的 proposal 机制类别确实不同（对照探针 §3
   的预期形状）。

## 9. 首跑现象记录（smoke，runs/seat-v6-smoke，$0.94 / 3 evals / 0.35h）

**核心目标在 root 第一次延伸处即告成立**：同一节点，三个席位，三份不同的
理解，三条不同的 proposal，三个不同的实验结果——

| 席位 | 理解的形成（working model 摘要） | 实验方向 | 结果 |
| --- | --- | --- | --- |
| G1 跨域移植 | grid_search 结构在 2M 次查找间重复——域外对应是数据库索引的离散化查找表 | 65536-bin direct-index 表 + 桶内线性推进，改 calculate_macro_xs 调用点 | **+24.2%** (1.82M lps) |
| G2 分解重组 | **实测分解**：toy 实验测出平坦二分 ~20 次 cache-miss 迭代、占 ~37% runtime | 沿值轴重切两阶段：粗定位（B=131072 桶表，init 预算）+ 桶窗内二分，grid_search 接口不变 | **+23.6%** (1.81M lps) |
| G3 理想化极限 | 极限论证：索引计算免费的世界里，二分是可移除冗余；表仅 256KB vs 240MB 网格 | K=1<<16 first-greater-than 表 + 有界线性校正，新函数 grid_search_table | **+8.0%** (1.58M lps) |

三条全部 bit-identical（VERIFY=998920）过 gate，root 长出 **3 个 depth-1
子节点**——v5 同场景只产出 1 条冠军提案的单链。每条 proposal 的
material_difference 都显式陈述本席位透镜（提交再锚定生效）。

诚实的边界观察：目标区域收敛（三席都打到 grid_search→LUT——这个小
benchmark 的最大热点只有一个），与探针 §3 预测一致（"同一场地、不同学派
的问法"）；但**理解的来源**（域外结构对应 / 定量实测 / 极限论证）与**实现
策略**（改调用点 vs 重写入口内部 vs 新函数；65536 vs 131072 bins；桶内
线性 vs 桶内二分）真实互异，且 8% 与 24% 的结果差距是真实的实验分辨。

§8 验收（scripts/check_seat_v6.py）：**全部硬检查通过**——多席位异透镜、
payload 三件套/无禁词、恰一 proposal id、not-bought 指名、谱系无重复、
cognitive_transformations 表 0 行、reframe skill 在册。诚实 quiescence 本
轮未被触发（supervisor 在两席在飞时正确地选择了空 purchases 等待）；
untried 耗尽路径由单测 test_empty_selection_completes_when_untried_exhausted
覆盖。

## 10. 实现记录（v6，2026-08-23）

代码面（与 §4/§7 一一对应）：

- **透镜基**：`generator.json` 换 v2 三件套；`Generator` 扩展
  directive/forbidden/self_check；`select_one_generator`（永远 G1 的
  first-untried）删除——透镜只来自 supervisor 的 purchase。
- **store**：`LeaseSpec.lens`；`allocate_proposer`/`commit_supervisor_decision`
  的 `max_proposals_per_node` 默认 None=无限（溶解）；透镜在**决策事务内**
  原子盖到 episode 上（`variation_operator`，一 episode 一透镜）；同决策
  重复 node_id 合法化（一决策买同节点多席）。
- **scheduler**：决策解析 `seat_purchases: [{node_id, lens}]`；`_seat_leases`
  只执法（容量/透镜存在/谱系查重/决策内查重），不选透镜；`_seat_episode_for_node`
  ——首席用节点从未分配的 episode，后续席位一律**新建兄弟 episode（不继承
  任何 sibling 会话）**；诚实 quiescence 在此执法：空 purchases + untried
  非空 + 无在飞 → 拒绝并回理由；wake facts 三块（seat_ledger / untried /
  lens_stats，lens_stats 含 best_gain 按目标方向计）；`runtime_facts` 换
  `seats_inflight`；proposer payload 携带 `seat` 三件套块（透镜身份），
  `suggested_operator_id`/`generator_basis` 移除；前代备忘带
  `originating_lens` 署名；空席位 abstain 的 scheduler 兜底（0 state 的
  abstain 恰好重试一次后放行，防死循环）。
- **proposer**：席位 system prompt = 身份第一行（G5（反演）seat of node X,
  identity not advice）+ 三件套 + 减薄 charter（prompts/proposer.md 重写为
  Seat Charter：职责一问、空席位备忘契约、备忘=署名学派可整体折扣）+
  认知纪律；协议块数量词零出现（submit_explorations 描述改为"你的
  proposal"+透镜自检陈述）；transform_worldview 全通路删除
  （cognitive_transformer.py 删除、工具 schema/handler/fingerprint 清除、
  `cognitive_transformations` 表 dormant 保 schema）；runtime guard：
  abstain 前必须已注册 research_state（协议纠正级），v5 蒸发式退场双侧封堵。
- **supervisor**：`submit_growth_decision {seat_purchases, rationale}`；
  supervisor.md 重写（三元决策 node×lens×数量、反单调警示、charter 纪律
  =点名 not-bought、空 purchases=等待/耗尽才完成）；facts 契约更新；
  `list_nodes` 带 seats_inflight + lenses_burned_here；node_allocations
  带透镜。`_protocol_reminder`/cold start 同步。
- **配置**：`max_research_per_node`/`max_proposals_per_node`/
  `generator_reseed` 从 EvolutionConfig 删除（旧 YAML 键静默忽略）；三个
  example 配置清注。`proposal_slots` 保留（仅 frontier 基线模式）。
- **测试**：243 passed（v5 基线 246：删 transform/select_one 旧测试，增席位
  契约测试——双席异 episode、谱系查重拒绝、诚实 quiescence 双向、空席位
  guard、payload 卫生）。

实现中补的三个设计外执法点（都是 §2.4"arness 只执法"原则的直接应用）：

1. **诚实 quiescence 拒绝路径**（§2.4 的机械面）：空 seat_purchases 在
   untried 非空且无在飞时被整决策拒绝，理由写进 rejection feedback——
   stillbirth 从"劝告"变成"不可能"。
2. **决策内查重**：同一决策里 (node, lens) 重复 → 拒绝（防同透镜双注）。
3. **open 座位透镜计入 burned**：席位 episode 在决策事务里已存在，天然进
   谱系 burned 集——并发买同节点不同透镜合法、同透镜非法。

v5 终局回填（反面教材定稿）：树臂 1.66h quiesce，2 terminal evals/$1.30
（预算剩 ~97%）。病理链与 §1 预测一致：研究三弃权 → 空 growth →
quiescence。旧架构的"合理等待"没有事实边界，v6 的 untried 集就是那个边界。

## 11. 4h 对比 run（runs/seat-v6-2h，2026-08-24，4.79× / 29 evals / $12.62）

与 ablation-v5 两臂（coding-agent、serial loop，同 benchmark 同 4h 累计
driver 时间）对照。配置 `examples/xsbench_opt/task-supervisor-v6-2h.yaml`
（scientist_steps=64、proposer inflight=4、experiment inflight=3、
top_k=6）；2h 墙钟处换 driver 续到 4h（`runs/seat-v6-2h.extend.sh`，剩余
秒数按最初起点算，终点钉死）。

**终值**（baseline 1,514,004 lps，全部 VERIFY bit-identical）：

| 臂 | 4h 终值 | evals | 花费 | 备注 |
|---|---|---|---|---|
| **seat-v6** | **4.79×**（7,246,377 lps） | 29 | $12.62 | 30 节点（d1×6/d2×12/d3×11），33 席位 episode |
| coding-agent | 4.21×（6,134,969） | 11 | $4.21 | 单 agent 长链，d1 一击 |
| loop 串行 | 1.94× | 9 | $4.47 | |
| tree（v5） | 1.85× | 2 | $1.30 | 1.66h quiesce（§1 反面教材；已被 seat-v6 取代，不入图） |

图（三臂：coding-agent / loop / seat-v6——**seat-v6 即 supervisor tree**，
v5 tree 不再单列）：`runs/figures/ablation-v6-worktime.png`（工作时间轴，主对比）/
`runs/figures/ablation-v6.png`（墙钟轴）/ `runs/figures/ablation-v6-cost.png`（成本轴）。
seat-v6 前三节点 4.79×/4.65×/4.62× 来自三条不同谱系（含一个 d1 直连
root 的 4.62×——决策 20 后 root 补席的直接回报）。

**supervisor 职责观察**（27 个 growth 决策全程）：

- 定价阶梯清晰：首轮铺三席 → 热点加席（exploit）→ 独立新枝（breadth，
  root-G4 一席产出 +81.2% 的 d1，全场最优投资）→ 对**树上位置**定价
  （"同样的钱早一步不如直接在最优上问"）。
- 负谱系翻案：−29.5% 子节点先被拒（无证据），恢复迹象（+37.8%）出现后
  立即三面下注——负资产被重新定价而非永久弃子。
- 透镜账本进入决策："G6 4/4 pass / best 81.3%" 粒度引用；决策 6 买战绩
  透镜（求稳）与决策 8 拒绝战绩透镜（"single strong result is a small
  sample"，求新）构成一对自然对照，两种风格各有在飞实验。
- 容量超买被拦 7 次（2/3/4/6 席 > 空位），全部在重试内收敛为合法决策；
  决策过期（事件批前进）2 次，均正常重议。改进项：payload 应显式给
  "空余席位数"事实以省拒绝对回合。

**深度 2-3 的 proposer 表现**：同父三席走出 2.2×（b0561935 →
4.64×/2.87×/2.27× 层内分化）；子辈席位精准吃掉父辈设计残留（G2 两阶段
桶表留下桶内二分 → G5 子席以 rank 表去之，+49.7%）；跨谱系透镜重组出
王朝（root-G4 对称性缓存 × G5 rank 表 = 2.74M → 4.11M → 4.64M 谱系）。

**发现并修复的 harness 缺陷**：`_seat_leases` 的 burned 快照在决策事务
外预取，批内"祖先买 L + 后代买 L"穿透（run 中 1 例：root-G8 与
b07b15d0-G8 同决策，episode d72a959a，产出 1 条 proposal）。修复为批内
对称祖先检查（与顺序无关）；回归测试
`test_lineage_dedup_within_one_decision`；checker（check 5）同时改为
购买时序重放语义（祖先后买同透镜 = 合法新问法，旧终态对比误报 16 例）。
244 tests 全过。4h run 数据按修复前如实保留该 1 例违例。

**已知问题（下轮再议，本轮零实际伤害）**：谱系查重现为祖先域语义
（ancestry 上任一票即烧），有两处过严：①祖先晚买透镜会把它烧给整棵
下游子树——root 01:47 烧 G6、03:06 烧 G8 后，战绩最好的 G8 在最后两
小时对全树不可雇（untried 静默缩水，无拒绝记录，纯机会成本）；②L 输
在祖先、后代走的是别的枝时也被禁——但 L 的答案并未进入后代的世界，
与跨谱系移植同价。正确语义应为**具身查重**：L 在 N 被禁 ⟺ L 参与建造
了 N 的世界（N 自身跑过 L，或 N 路径上某节点由 L 的席位产出）。单调
钻探在具身语义下仍被完全挡住（链上步步具身），v5 兜底不损失；长程风
险方向是现行规则可能让程序在新鲜 (透镜×材料) 组合尚多时提前"合法穷尽"
（untried 缩水过快 → 过早 quiescence）。本轮 4h 内 7 次拒绝全为容量、
查重从未实际出手，故不实现，仅记录。批内互斥若随之放松为同节点去重，
需与 §10 的回归测试同步改。

时间轴口径补充：`ablation-v6.png` 是墙钟轴（含两臂中段被会话重启杀掉的
~2.5h 死等，臂间不对齐）；`ablation-v6-worktime.png` 是工作时间轴
（plot.py `x_axis="worktime"`：日志 elapsed 时钟按重置分代 + mtime 锚末代
+ DB 行定各代边界，死等折叠为零）。工作时间重建与各臂日志累计精确一致
（coding-agent 4.21h、loop 4.08h、tree 1.66h、seat-v6 4.38h 含 2h 处
换班的 5 分钟空档）。

### 11.1 事后修正：coding-agent 臂的真实机制与平台期病理（2026-08-24）

§11 初版把 coding-agent 的平台期写成"绕着冠军重写、逃不出范式"——
事后核对 DB 提案/实验时间线，**机制与此不同**：该臂的 no-op proposer
瞬时完成，在 frontier 尚为 bootstrap root 的头几秒内按配额一口气发布
9 条锚定 root 的提案入 FIFO 队列；实验槽 inflight=1 串行消化，4h 只跑
完 11 条（root×9 + 1.71M 首子×2）。全 run 共发布 2680 条提案、2669 条
未及运行，其中 2644 条锚定 6.13M 冠军（+111m 起 frontier 持续指向它，
但队列从未轮到）。三个事实：①实验世界 sha 固定在
root，从不在 best 上；②跨轮信息只剩指令里提案时刻的过时标量（exp8
执行时仍被告知 best=1.46M，实际树内 best 已 6.13M）；③**6.13M 冠军
从未被用作父代**（其队列提案 4h 到点未轮到）。故该臂实为 11 次对
baseline 的独立重写，4.21× 是独立抽签的最大值，平台期是"无积累"而非
"范式锁定"。loop 臂核对为真链式（exp3-9 全锚定当时 best），其平台期
维持"研究员范式锁定"的诊断（峰后 6 提案同族微手术 + 一次 init 作弊
未遂、被 RATE_PLAUSIBLE 击落）。

**ablation 解释力 caveat**：coding-agent 臂一半死于 harness 人工痕迹
（瞬时 proposer + FIFO + 提案时点快照），非纯粹 coding-agent 范式失败；
公平 baseline 应令执行者每轮见当前 best 代码与数值。若复跑：proposer
配额按实验完成节流（提案在实验空出时才发），或在提案消费时重锚定到
当前 frontier。seat-v6 不受此影响（席位 payload 消费时构造，锚定被买
节点 + 前驱档案，队列深度恒 1）。

## 12. 持续 coding-agent 臂：一个 Claude Code，一个世界，4 小时（2026-08-24）

§11.1 的公平 baseline 兑现：`scripts/run_cont_agent.py`。执行者本来就是
一次 `claude -p` 会话（experiment/agent.py），此前每 ~11-25 分钟一换、
世界即弃、情报过期。本臂改为**单会话单世界跑满墙钟**：

- **一个会话**：一次 claude 调用，timeout = 剩余墙钟（4h − 启动开销）。
  上下文压力由 CLI 自身 auto-compact 处理；会话世界**整仓可写**挂载
  （标准 executor 是 /work 只读 + src 可写——那会让自测 bench 写不进
  build 产物）。agent 可随时自跑 `check_verify.sh`/`bench.sh`——旧臂
  的 executor 从未见过自己的分数，这是本臂补上的反馈回路。
- **快照不静音**：harness 每 5 分钟用临时 GIT_INDEX_FILE 把 src/ 冻成
  侧链 commit（agent 无感知；pathspec 只含 src，PROGRESS.md / build
  产物 / 对冻结脚本的篡改永远不入档，replay 永远跑干净 harness）。
  src 树无变化则跳过该拍。
- **监测三层**：①harness 视角——每第 2 个有效快照在**另一颗核**
  （core 11）上即时试评，run.log 出 `LIVE snap#N: X.XXx gate=…`（指
  示值，与 agent 自测竞争内存带宽）；②agent 视角——stream-json 逐事
  件落 trace，工具调用与其自测数字可 tail；③叙事线——prompt 要求在
  仓库根维护 PROGRESS.md（src 外，永不入档），一行一测。
- **终局权威回放**：班次结束后，每个有效快照在空闲的 agent 核上按序
  过冻结 gate + bench，回填为标准 proposal/experiment/node 三元组，
  `created_at` 回溯到快照时刻——run-dir 与其它臂同构，直接入图。
- **记账重建**：usage 不再依赖终局 result 事件（被墙钟杀掉的会话一
  无所有，正常结束也只有单点）；改为从 trace 的 assistant 事件逐事件
  求和回填 usage.jsonl，时间戳按事件序线性插值（近似，成本轴由单点
  变全程曲线）。注意：此记法与其余臂（终局单事件 usage）口径不同，
  跨臂成本对比时以本臂更接近真实 API 计费。
- **入图替换**：旧队列雪崩 run 挪至 `runs/ablation-v5/coding-agent-queue/`
  留档不入图；新 run 落 `coding-agent/seed-1`，图仍三臂——coding-agent
  （本臂）/ loop / seat-v6。本臂 run.log 刻意不含 `elapsed=` 模式，plot
  的工时重建自动落到墙钟轴——本臂无空挡，墙钟即工时。

冒烟（4 分钟班）：48 个 assistant 事件 $0.18、3 快照、0.97×→1.10×
（中途回退被快照诚实记录）。正式班 2026-08-24 启动：baseline
1,345,895 lps，4h 墙钟，预算帽 $30。

### 12.1 首班结果（2026-08-24，1h 自收工）

| 臂 | 终值 | 工时 | 花费 | 行为 |
| --- | --- | --- | --- | --- |
| coding-agent（持续班，本节） | **2.08×**（2.79M） | 0.99h | $0.86 | **自愿收工**，3h 预算未用 |
| coding-agent（旧队列臂，§11.1） | 4.21× | 4.21h | $4.21 | 11 次强制重启 |
| loop | 1.94× | 4.08h | $4.47 | 真链式，范式锁定 |
| seat-v6 | 4.79× | 4.38h | $12.62 | 全程买席位 |

12 快照全 gate-PASS。四步机制与 seat 早期谱系同梯（增量 LCG 种子 →
分桶查找 → 双趟预取 → 计数排序重排），每步带 bit-identity 论证，
SELF_REPORT 质量高——**它不是能力不足，是满足型（satisficer）**：
"Both gates pass on a clean rebuild. The work is complete."

三个读数：
1. **单会话的极限形态是"高效爬梯 + 早退"**。没有人在场问"就这些
   吗？"——supervisor 架构的存在意义恰好是永不停止这个问题。seat-v6
   同期（1h）2.24× 与本班 2.08× 几乎同速，其后 3h 的 2.24→4.79× 全
   来自"被要求继续"。
2. **重启压力 > 自愿持续**：旧队列臂的病理（9 条冷启动提案强制灌入）
   反而以 11 次重抽拿到 4.21×——粗糙的外部压力胜过 agent 的内在动
   机。持续班给它世界、反馈、时间三样齐了，独缺"不许停"。
3. 成本侧持续班最优（$0.86 → 2.08×，每美元 2.4×，seat 为 0.38×/
   $）——若任务只要"快速到一个不错的解"，单会话是最高效形态；
   议会买的是它不生产的东西：不满足。

诚实边界：n=1、deepseek-v4-flash effort=low、本 prompt 措辞（"pace
yourself"未强压"用满时间"）。"4h 授权下自愿干多久"本身是被测行为，
不是缺陷；若复跑可加一条"finishing early is failure"对照臂。

**自愿还是优化不动了？**（trace 终段取证）自愿。三证：①整班
PROGRESS.md 零 REVERT、零 VERIFY 失败——它撞过零次墙；②退出发生在
**最大一跳（计数排序 2.1×）之后**而非收益枯竭处（+3% 边际低谷在突破
之前，t7）；③终段推理是"final review → 干净重建复验 → The work is
complete"——seat 后期靠的 rank 表、桶内低/高端共享、对称统一等下一
梯级完全未尝试。它的任务框架是"把内核正确地变快"：达成即完备，
3.8h 是预算不是配额。图上已标注：`self-terminated @0.9h (no data
beyond)` 的虚线垂标 + best-so-far 平线铺满全横轴——平尾读作"闲置"
而非"卡住"。

### 12.2 第二班（continuation）：压力下的满足型（2026-08-24）

同 run-dir 从 d12 尖端（2.79M）续班：继承世界 + 注入第一班 SELF_REPORT
全文作交接档案 + 明令 "work the ENTIRE shift; finishing early is a
failure mode"。结果两读：

- **性能**：4h 授权下又干 ~40 分钟，2.79M → **6.7M 峰值（5.00×，统一
  core-9 口径）**，终值 6.54M（4.86×）。绝对值 6.54M vs seat 7.25M
  （-10%）；×值 5.00 vs 4.79（本 run 基线 1.346M 低于 seat 的
  1.514M，跨 run 噪声内基本平手）。总成本 **$3.06**（seat $12.62 的
  1/4）、总工时 ~1.66h（seat 4.38h 的 38%）。
- **行为**：显式抗早退指令**没有改变退场时刻**（第一班 57min、第二班
  40min，均在"最大一跳之后 + floor 论证"处自认完备）。压力改变的是
  **到达的高度**（2.08×→5.00×），不是**停留的时长**。满足型不是 prompt
  能修的，是分布属性。

第二班的方法学增量（seat 未走过的新梯级）：pass2 顺序缓冲直排
（2.34→3.53M）、pe-bucket 计数排序 + 单调 idx 扫（免去 pass1 二分）、
**AVX2 向量化 4 通道插值（逐 lane bit-identical，不用 FMA）**、
NuclideGridPoint 通道前置 + 64B 对齐。被拒实验亦有账（对齐 load 段错误
自修、非时间性存储回退等）。

**终局对比表（4h 同 benchmark，统一口径注记）**：

| 臂 | 终值 | 绝对 lps | 成本 | 工时 | 退场方式 |
| --- | --- | --- | --- | --- | --- |
| coding-agent（两班+手动点火） | 5.00× | 6.73M | $3.06 | ~1.7h | 两班均自收工（40-57min） |
| loop | 1.94× | 2.27M | $4.47 | 4.08h | 跑满（范式锁定） |
| seat-v6 | 4.79× | 7.25M | $12.62 | 4.38h | 跑满（买满席位） |

操作痕迹如实记录：两班之间的人工再点火（判断、档案、压力）= supervisor
职能的手工扮演，且第二班 prompt 含系统外信息（"别人到过更高处"）；
班中 midrun 五拍曾用 core-11 评（偏高 ~7%），终局已统一 core-9 重评。
驱动器班末重放因运行中进程无 sha 去重撞 UNIQUE 约束崩过一次，收尾入库
由手工完成（脚本已补 get_node_by_sha 去重）。

**结论重述**：连续性 + 廉价再点火 ≈ 议会性能的 90%+，成本的 1/4。议会
的剩余价值不在速度在保障：自主持续（不依赖外部点火）、多谱系并行
（单线中断即全停）、以及操作者盲区免疫。最小修正方向：把"再点火"做成
harness 内的机械规则（班次结束 → 档案自动交接 → 新会话强制满班），
议会收缩为"点火器 + 一两席对冲"。

## 修正案（2026-08-29）：席位纪律改由工作区强制，不再靠弄瞎 agent

**事故**：jrb-full-std 首对 std run（2026-08-28）里两次 proposer 会话
全部烧毁——但根因不是 900s 太短。认知席位工具面是
`Read,Grep,Glob,WebSearch,WebFetch,Task`（无 Bash/Write），而 brief 明令
"establish with evidence ... prototype in /scratch and report"；npz 数据
Read/Grep 读不了，python 全被 "requires approval" 挡死（002 撞墙 28 次、
004 撞 34 次，各花 ~11 分钟 grep 自己的 transcript 调试权限系统），到点
SIGKILL + `{"error": exceeded time box}`——**900 秒的思考一个字没收割**
（004 的遗言本身是一段有价值的诊断）。对照组：executor（全工具）零拒绝、
盒内交付 ×2，证明工具面是唯一变量。XSBench 那笔"900s consult timeout
杀席位"的债，根因同源，至此还清。

**修正（scientist/assistant_tools.py，用户拍板"隔离副本+全工具"）**：

1. **全席位全工具**：`_COGNITIVE_TOOLS` 补齐 Edit/Write/Bash。席位身份
   = prompt + 产出契约（digest 唯一回传通道），纪律 = 工作区。
2. **认知席位跑世界的一次性 fork**（`_fork_world`）：小树（src/scripts/
   .git/docs）真复制可自由改；数据级目录（`benchmarks` 或 ≥512MB）符号
   链接进只读正本——**写穿透被内核 EROFS 拒绝**，9GB 包成本 0.01s。
   .scientist 从不随 fork 出海（沿袭 executor-isolated 的"给世界不给账本"）。
   proposer/challenger fork **当前**世界（提案必须看见现任 solver），
   executor-isolated 同步升级为 fork（原 9GB copytree 太贵），searcher
   read=lab 也走 fork（read=node 的 /repo 本就 ro-mount，直用）。
3. **超时不再清零**：SIGTERM→10s 宽限→SIGKILL；`_partial_report` 从残
   transcript 收割最后一段实质文本 + 工具计数，标 `timeout-salvaged`
   （崩溃同理 `crash-salvaged`）作为报告交回 PI；仅当残卷无内容才落回
   旧式裸 error。
4. **认知席位时间盒 2700s**（spec `budget.cognitive_timeout_seconds` 可
   调）：带证据的提案本来就是最贵的环节；searcher 维持 900s，executor
   沿用按需 timeout_minutes。完全无盒不采纳——主循环 wait 停在同事身上，
   一个挂死席位会悄悄冻住整个 scientist；有了收割，宽盒才安全。
5. **席位契约随 prompt 声明**（`_FORK_NOTE` 等）：一次性副本、全工具、
   只交报告——不再让模型靠撞墙发现边界。

自测：fork 结构（symlink 绝对化后）/ .scientist 排除 / salvage 在真实
被杀 transcript 上解出 105 工具调用 + 临终诊断。容器内 EROFS 语义由
现有 ro-mount 契约保证（smoke S2/S9），无新机制。

**第二轮（同日深夜）：全席位审计——接线清零**。顺着"权限是否配得上职责"
过了一遍四角色，再修两处脏线：(1) **`_SEAT_TOOLS` 单一工具面**（executor
也补上 Task；认知/执行不再有第二套常量）；(2) **`timeout_minutes` 四角色
全认**——PI 侧 schema 早就给 searcher/proposer/challenger 暴露了这个参数，
但 `engage()` 只读 executor 的：PI 买 60 分钟、席位 15 分钟死（接线谎言），
现统一走 `_box_from_action`（clamp 1–180min，缺省=角色默认：searcher 900s
/ 认知 2700s / executor 30min）。searcher read=none 的提示从"文件系统式
声明"改为"任务式声明"（literature-only，不谎称做不到的隔离）。终态接线：
**一个工具面、一个 fork 帮手、一个时间盒解析器；纪律=工作区+digest**。
自测：stub ledger/world + 秒退假席位进程，四角色 engage→fork→spawn→poll
全链 + 盒参数（默认/加购/顶格/非法 read 拒绝）全过。未开的三样与理由：
无限时间盒（挂死席位会冻住主循环；180min 硬顶+salvage 已够）、
distill_word_cap 300 词（上下文经济学非权限；全文在盘 PI 可读）、
容器内代理（WebSearch 服务端执行必通；WebFetch 站点级自适配，不接脏线）。

**第三轮（同日，返回值全链路 + 大项目连续性）**：顺着"返回值截断类风险"
把席位→PI 的每条信道过了一遍，修四处：(1) **PI 消息丢指针**——
`_collaborator_report_message` 此前不送 truncated 标志/全文路径/保留工作区/
follow-up/超时标记，"PI 可读全文"是死接线；现全量入消息。(2) **artifacts
路径造假**（硬编码 /work/.scientist/scratch，真实是 world.scratch=/scratch）
→ 改用世界真值。(3) **结构化字段抽签**——_parse_tail 只认文末 json 围栏而
席位 prompt 从没教过格式；现席位 prompt 显式规定文末 fenced JSON 七字段
（report_digest/diff_summary/metrics/evidence/artifacts/uncertainty/
recommended_follow_up，"prose outside is archived but not delivered"）。
(4) **distill 默认 300→600 词**（spec 可调），且截流必附 transcript 指针。
大项目连续性两件：**成功的 fork 不再删除**（续作席位拿到前任工作区指针；
大项目=席位接力序列：摸底→提案→建造(executor-current 的 src/ 天然持久)→
攻击）；**顶格时间盒改 spec 参数** `budget.seat_timeout_max_minutes`
（默认 180，大项目写 480 即可买 8h 席位，代码不再藏顶）。salvage 加固：
临终文本 <200 字符时自动并入前一段实质文本。五组自测全过（含
prompt→_parse_tail 回环、消息完整性、成功 fork 保留）。

**第四轮（2026-08-29 深夜终局）：协作者运行时重写为同步——根上消除整类病**。
用户裁决正确：前三轮的补丁里，wait 执法、孤儿对账、shutdown 收尾修的全是
**异步骨架自己的病**（席位后台跑+邮件回投）。实践中席位几乎全串行使用
（jrb run 13 次 wait vs 5 次派遣），异步买到的并行是纸面的。重写为
**同步**：`engage()` 阻塞跑完整个席位会话，报告**就是 tool result**——
给命令→跑 claude→响应→返回内容，四步。构造上消失的病：超时执法点唯一
（阻塞 wait 的 timeout 参数）；崩溃=finally 内联清理，无孤儿可对账
（仅 SIGKILL 科学家本人留一个窗口，_reconcile 用 proc.pid+cmdline 守卫
收割）；wire 不变量自动满足（tool_call/tool_result 原生配对）；PI 零新
概念（席位=普通工具，不再学"邮件/wait"约定）。删掉：`_Job` 队列、poll
泵、wait 工具、finished_pending、any_over_box、shutdown 整段。保留
（正交好部件原样）：fork 工作区、四角色 mandate、fence 报告契约、
salvage、三层时间盒、消息完整性、fork GC（加 3.5h 年龄守卫）。真并行
保留在唯一被实际使用的形态：**同回合多席位调用线程池并行、整批归位**
（~25 行，无常驻状态）。七组自测全过：正常返/超时收割（2s 盒 30s 睡
席位 2.0s 返回+诊断 harvested）/崩溃收割/起不来回执/孤儿对账（counter
跳过已用序号防 raw.txt 截断）/fork GC/同回合并行（2×3s 席位 3.0s 完成）。

**第五轮（同日）：限额全面放宽——"放开手脚"姿态**。用户裁决：保守限额
在扼杀长跑大项目。新默认（代码默认+std spec 同步+PI schema 上限）：
searcher 盒 15→30min、proposer/challenger 45→90min、executor 默认 30→60min、
单席位硬顶 180→**480min**（schema maximum 同步 480）、distill 300→1200 词、
evidence index 60→100 行（取最新）、PI bash 输出截 12k→40k 字符、代码默认
命令超时 360→1800s、fork 保留 12→24 份、GC 年龄守卫 3.5h→8.5h（必须大于
最大盒）、salvage 切片 2k→4k。不动并说明理由：compaction（上下文管理非
能力限制）、idle turns / handover 词帽（行为纪律与退出契约）、steps 3000 /
wall 7d（本已宽裕）。风险如实记：同步模型下挂死席位最长占住 PI 一个盒
（480min），墙钟看门狗与到期收割兜底。自测过（含 480 顶格与 700 钳制、
fork 守卫>8h、新默认 dispatch）。

### 修正案第六轮（2026-08-29）：默认盒再放大方一档

生产证据（jrb-full-std-elec-sync-scientist）：executor-003 的 per-PMT 时间
偏移标定 60min 默认盒装不下，撞盒 salvage 后该线留成 open question——
PI 派单时按默认给盒，而默认值本身成了瓶颈。真实席位用时呈双峰：中位
<40min 干净交付，长尾（标定类大活）>60min。裁决：默认值贴着长尾给，
不贴中位数。searcher 30→60min、executor 60→120min、proposer/challenger
90→180min；schema maximum 与 spec 钉值同步。代价不变：挂死席位最长占
PI 一个盒（480min 硬顶未动），salvage+墙钟看门狗兜底。

### 修正案第七轮（2026-08-29）：reviewer 席位 + 弹药可见性 + 听证门

生产证据（jrb-full-std-elec-sync-scientist）：PI 在杠杆名单非空、预算用
4% 时收笔（62/3000 步、3h/7d）；历史证据 seat-v2 cut_off→delivered
+36.8%——自判"完成"系统性偏早。三件套全部环境侧，零内容判断，遵守两
条新原则（harness 只提供环境不插手内部决断、可强制知情程序；agent 不
是婴儿、信息自取不喂饭）：

1. **reviewer 席位**（第五角色，建议权）：PI 亲笔汇报入门（自由形式，
   无四问 schema），全工具自由翻找；fork **包含** `.scientist` 运行记录
   （`_fork_world(include_ledger=True)`，唯一带账本出海的席位——reviewer
   的职责就是看历史，读多少它自己定）；prompt 零注入（无 judgment、无
   证据索引——汇报是主张、工作区和记录是事实）；认知盒默认 180min。
2. **弹药可见性**：`_budget_note` 剩余优先（"[budget] 91% of the run
   remains: step 250/3000, 152.9h of the 168.0h wall left"），step 1 与
   每 50 步 `_nudge` 一次。**零指令动词**——仓库先例（_KILL_KNOCK 注
   释）：义务式/倒计时文本曾让 stopping 更糟被删；本注只陈述剩余，不
   说该干什么。
3. **听证门**（listen-before-deliver，Write 前必须 Read 的知情程序）：
   `validate_conclusion` 扩展——deliver 须有 reviewer engagement 在
   `src/` 最后改动之后 finalize（`reviewer_heard_after(_last_src_write)`，
   纯时间戳；salvaged 也算听过）；拒绝消息人话+弹药快照；**三振熔断**
   （同 episode 三次拒绝后 `listen_enforce=False` 放行，action_log 记
   deliver_listen_overridden）——是回心转意的机会，不是硬闸门。abstain
   不设此检；无 world/assistant 传入时门 inert（host 自测兼容）。

接线：`NATIVE_TOOLS`/`NATIVE_FORWARDED_ACTIONS` 增 reviewer；cold start、
runtime/concluding block 的角色列举同步；deliver_world 工具描述披露门规
（程序透明）。六组自测过：prompt 反喂饭断言、fork 双模式、门检四路+
熔断、弹药注无指令动词、_last_src_write 空世界 0.0、注册面。

**第七轮补记（同日）**：(1) 措辞修正——reviewer 非出口专用，三个自然
时机（大战役/里程碑后、疑向时、收笔前）；机制本就无时点限制，修的是
描述与 runtime block 的框定。(2) **live 探针终验**（runs/probe-reviewer-
live，真实 engage() 路径 + 终局 run 完整世界含 37M 记录 + PI 口径汇报，
1132s done）：reviewer 重跑 solver 逐位复现三指标、亲测双 gate、逐条对
digest 核 dead_ends；判词命中要害——"'energy is at its CHARGE-based
floor'(真) 被硬化成 'energy is done'(假)"；且自带新实验产出：occupancy
独立能量观测量（GLS 融合 2.08→1.92% truth-free 成立）、timing 6× 余量
（0.97ns truth-free）、用噪声计算杀死 handover 留的 per-PMT 偏移杠杆
（8.5ns 误差>>1ns 偏移，不可标定）。回心转意机制体外验证成立：此报告
若落在 step 50 而非死后，6.9 天预算足以吃掉全部三条。七字段 fence 两
字段未填（uncertainty/follow_up null）——单样本，暂不改文案，成惯例再
点名。

**第七轮生产终验（2026-08-29，runs/singlenode/jrb-full-std-elec-r7-scientist）**
：三件套（reviewer+弹药注+听证门）首次同场实战，test **2.03%/59.7cm/
0.553ns**——能量轴首次压过 coding 臂（2.05%）成全臂最优，timing 从两代
弃权变真重建。链路全绿：

- **reviewer 双用法齐备**。step 59 中途里程碑式（首战三席位整批归位后）：
  PI 汇报词诚实上桌（含决策点"值得再挖吗/会不会在 test 上碎"），reviewer
  27min 回读——独立复现、纠地板数（0.0149→0.0144）、杀三杠杆（SPE 计数
  0.0315/prompt 门控无增益/drift 项过拟合）、旗 vertex 活口与 drift 风险；
  step 68 收笔前：亲手测 val 更香的交互项（0.01748<0.0185）用 20-seed CV
  杀掉，确认"可达地板"。PI 对 reviewer 判词的处置是本轮最好看的一笔：
  004 的"能量地板"被 executor-006 的暗计数扣除推翻（0.0206→0.0185）——
  判词是可证伪主张非圣旨，PI 拿它当矿图不当当票。
- **门检零触发**：PI 在最后一次 src 改动后主动买票（原话 "as required
  before deliver_world"），程序被内化成习惯——知情程序的最好执法是无需
  执法。
- **弹药注被自引**：PI 汇报词里出现 "~95% of budget left"。
- 席位面：7 席（executor×3+proposer+challenger+reviewer×2）首购
  proposer；同回合三席并行整批归位；零 salvage 零退回零异常；69 步
  4h49m。转移自校准：handover 自报 ±0.0022，val 0.0185→test 0.0203
  落带内。弱点：vertex 转移衰减（50.4→59.7），coding 33.8 仍领先。

---

## 第八轮（v7）：并行与续用——席位接口向「委托-信任」关系升级（2026-08-30 定案，待实现）

### 动机与证据

omilrec v1.0.0 双臂 run（runs/singlenode/omilrec-v100-r1）的效率解剖。
coding 臂 4.8h 自判收笔于 3.67×；sci 臂同窗只到 2.07×（后 2.30×）。墙钟
去向五项：席位内循环（全量 `cmake --fresh` 重建 1-2min/次）、**席位串行**
（一个 2h 盒，PI 回合被同步劫持）、每席位重新定向（重读 5k 行源码）、勘察
期无界搜索（66min cvmfs 爬行）、官方 eval 5-8min。方法学判定（讨论定案）
：非无效脑暴——记忆账本 R1-R18 零重复、家族级证伪（一次实验关掉一整族）、
机理级否定（非单调性诊断 Minuit 盆地切换）；**慢在串行与管理成本，不在
搜索质量**。设计目标因此是消等待与重学，一字不动验证严格度。

### 北极星（用户定案）

**sci 与席位的关系，尽可能模拟用户本人用 Claude 的方法**：可以开更多
claude 并行；可以重开窗口 resume 并告诉它"之前我改了什么"；材料放那
席位自己会查。协调走世界（文件/git），不走 harness 消息通道——与
"Evidence & World 不动只被引用"同根。

深层理由（接口即教学）：PI 预训练里读过海量人类管理 Claude 的交互，
委托-信任的管理能力是现成的；接口讲它熟悉的语言，能力免费到账。现接口
（发简报→同步等报告→串行）教的是**工单队列**——PI 不并行的根因不只是
缺杠杆，是接口在教一种更穷的关系。

### 六条原则（宪法条款）

1. **harness 零内容**：不自动附变更单、不替 PI 排程、不裁决哪个 fork
   的活进主线。简报质量是 PI 的手艺：坏简报→席位验证失败→gate 拒→
   PI 学乖——损害被第一性基础设施（gate+冻结基准）封顶。
2. **一个世界一个写者**：并行 executor 全走 fork；主世界永远只有 PI
   一双手。
3. **新鲜感分工**：executor 可续用（craft 累积是纯收益）；
   challenger/reviewer 永不续用（独立视角是其认识论价值，续用=带前见
   审查）。
4. **聆听是专门的认知行为**：reviewer 同步串行。用户原话：要认真聆阅
   阅卷，不能交头接耳——若 reviewer 报告混进异步事件流，"听"会退化成
   扫一眼摘要放行，listen-before-deliver 的门空转。专注写进身份层，
   **不设 harness 闸**（执法化会把聆听做成打卡）。
5. **效率=第二性**：只在第一性（研究可靠性）留下的自由度内起作用——
   并行独立验证合法，砍验证是背叛。立在场外 prompt 身份层，不在
   harness。
6. **harness 只造杠杆**：异步机制、resume 管道、fork 卫生、核隔离——
   纯环境，零判断。

### 映射表（用户↔Claude ⇒ PI↔席位）

| 用户与 Claude | v7 对应 | 机械结构 |
|---|---|---|
| 派后台子代理，不阻塞，完成时通知 | engage 异步化 | dispatch 立即返回句柄；报告作为完成观察落在 PI 后续回合；一次可派多个 |
| SendMessage 续接子代理（上下文保留） | continue_engagement | `claude -p --resume <session-id>` 原生支持；session_id 存 engagement 记录 |
| 子代理只回终报，全 transcript 可查不默认进上下文 | digest + raw.txt 指针 | 已如此——并行化后这是防报告洪水的关键 |
| 多窗/worktree，协调走 git | 并行 executor 走 fork | fork=worktree 模式语义（§3.3） |
| "我改了 X"由用户说，其余 Claude 自查 | 变更简报 PI 写；席位自审 | 零 harness 内容；世界带 .git |
| Compact 后靠摘要+memory+重读世界恢复 | PI 靠 wire resume；席位靠 continue | 与持久化契约对称闭环 |
| Monitor 盯世界，事件推送 | 席位完成即事件 | 回合头部一行席位状态（running/idle），环境信息非判断 |

### 三件机械改动

**3.1 非阻塞 dispatch（最大件）**。现状 engage 在工具调用内同步跑满
盒子，PI 回合被劫持 2h。改为 fire-and-return：句柄立即到手，PI 继续想
自己的（其 bash 实验在主世界照跑——委托后继续想，这才是 PI 的工作
方式）；完成报告作为观察事件落在后续回合。全角色适用，**唯 reviewer
同步**（宪法 4）。并发完成互不阻塞；digest 仍为默认回传面。

**3.2 continue_engagement（executor 专属）**。`claude -p --resume
<session-id> "<PI 的消息>"`：会话库按 cwd+config 派生，席位 side_dir
与 CLAUDE_CONFIG_DIR 均 run 级持久，原样传回即续用。resume 之后发生
什么全在 PI 的消息里——变更简报（"你走后世界改了这些"+下一步）是 PI
写，席位也可自行 `git log/diff` 审查。工程要点：session_id 已在席位
stream-json 每行里，存 engagement 记录即可；实现时验一次同 cwd/config
的 resume 匹配。**v7 只给已完成席位的续用**；对运行中席位中途改指令
（SendMessage 对运行者语义）风险加倍，留 v8。

**3.3 fork 并行 + 报告带补丁**。fork 语义=worktree 模式：隔离可写副本、
独立实验、经世界自己的 git 合并回。v7 机械形态保持整树 copy（修正案
的 `_fork_world`：小树真复制+数据符号链接）；git 纪律定死——**fork 在
自己副本上提交，报告附 `git diff`/HEAD，PI 手 apply、PI 手重验**。合并
是研究决策，harness 不碰。此任务域合并语义干净（bit-exact 改动天然
可组合：各自保输出则联合保输出，文本合并+一次全量验）。防互污：bench
用 TASKSET 钉不交叠核段（两臂对 pin 的机制已存在），构建/验 gate 阶段
随便并行。真 git worktree 化（共享对象库、fork 更轻）留作磁盘/时延成
真问题时的一步——语义不变，形态可换。

### 身份层措辞（prompt 变更点，落笔在实现轮）

- 效率第二性的精确画线："可靠性第一，效率第二；并行独立验证是效率，
  砍验证是对第一性的背叛。"
- 阅卷时刻的专注："reviewer 阅卷时你在听，别的都不做；听完消化完再动。"
- 委托模式："独立假设各派一个 fork 席位并发去验；等结果的时间是你
  自己的。"
- 续用简报手艺："续用老席位时，告诉它世界自它离开后变了什么。"

### 不做的（划界）

- 上下文继承席位（fork subagent 型分身）——PI 的 bash 就是它自己的手；
- harness 生成的任何简报/排程/裁决/变更单；
- reviewer/challenger 的并行与续用；
- 运行中席位的中途改指令（v8 再议）；
- 真 worktree 化（触发条件再动）。

### 铺垫件（正交，随时可落）

ccache 进容器（席位内循环重建降 3-5×）；任务仓库 `--suite fcn` 单套件
快验（~2min，两臂同权——基准工具面非喂饭，中间态快验+提交档全量的
quick/acceptance 二分）；cvmfs 遮蔽 EXTRA_MASK_BINDS（杀暴露面+无界
爬行双收益）。

### 验证计划

1. 假 claude 脚本（fake-claude 模式）：异步 dispatch 不孤儿、并发
   完成各自回执、continue 真传 `--resume` 旗标（argv 断言）、fork
   期间主世界零写入、报告带 diff 工件；
2. 回归：同步路径、salvage、GC、listen-before-deliver 门在并行世界
   不弱化（审后改动必重听仍有效）；
3. live 探针：一回合双 executor 并行 + 一例 continue 续用；
4. 真 run 对照（同任务重跑或下任务域）。

### 与既有轮次的关系

- 第七轮 listen-before-deliver：reviewer 同步保留、`reviewer_heard_
  after(last_src_write)` 不动——并行化不碰这扇门；
- 修正案 fork 机制：v7 直接复用 `_fork_world`，只加"报告带补丁"契约；
- 持久化契约：席位级 continue 与 PI 级 wire resume 对称闭环；
- 研究记忆层：席位继续精确携带 research_memory.jsonl（P4 指给看不
  喂饭，不变）。

### 讨论 provenance

本会话（omilrec run 盯守期间的三轮设计对话）：效率解剖与五杠杆提案；
"无效脑暴还是有效但慢"的轨迹判定；北极星定案（用户：模拟我本人用
claude 的方法）与三问三答（异步范围/resume 原生性+fork=worktree/
reviewer 同步聆听）；否定"harness 自动附变更单"（喂饭的体面姿势）。
