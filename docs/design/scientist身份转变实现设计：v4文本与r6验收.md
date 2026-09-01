# scientist 身份转变实现设计：v4 文本与 r6 验收

2026-09-01。上游共识：scientist身份转变定稿：三层责任结构.md（423b39b）。
本文是实现设计：落点、逐句草案、验证阶梯、r6 配置。专家送审搁置，
草句的仲裁人改为用户本人——文学标准不变（描写不立法、自洽、像人话）。

---

## 〇、总原则与一个设计发现

1. **纯文本轮，零机制改动。**四条 delta（先开后读/整目标满盒/外部视角/
   自由段不插手）全部是派遣层的行为，全部由文本承载。不新增工具、不动
   执法路径——harness 只提供环境。
2. **设计发现：宪章核心零改动。**prompts/scientist.md 第一段本来就是
   流向控制的定义句（"recognizing when progress calls for a different
   question..."），report-as-testimony 也在。背叛层级的是派遣层（box 描述
   的预算语义 + delegation 技能的缺口）。本轮只修派遣层——这符合租金
   纪律：已在位且付过租的句子不动。
3. 版本称呼：本轮文本 = **charter v4**（v3 租金审计之后的第一轮语义增补）。
4. 每句草案附租金论证（它改变哪条 delta / 哪条防退化条款）；删掉它行为
   不变的句子不进。

## 一、改动面清单

| 文件 | 改什么 | 服务于 |
|---|---|---|
| scientist/research_skills/delegation.md | Framing 节重写两段 + Watching 两处增句 + 两条边界句 | delta 1/2/4、防退化 1、边界修正 ①② |
| scientist/native_tools.py | ① timeout 描述×5 处换保险丝语义（抽共享常量）② executor 描述加整目标从句 ③ revise_research_state 描述升态势 ④ NATIVE_RUNTIME_BLOCK 加一句身份宣言 | delta 2、View 升级、层级宣言 |
| scientist/prompts/research_memory.md | View 段加态势定义句；Memory 段加 why-changed-mind 句 | View/Memory 语义升级、防退化 2 |
| scientist/prompts/scientist.md | **零改动**（见总原则 2） | —— |
| scientist/prompts/research_team.md | **零改动**（边界句进 delegation.md，理由见 §二.5） | —— |

工具面其余零改动：challenger 描述已含 "attack the current judgment"（覆盖
防退化 3 的靶子）；wait/cancel/continue 描述已是事实化动词；STUCK 自报与
handover 的 dead_ends/open_questions 结构已存在（演绎里的 stuck 账本有承载）。

## 二、逐句草案

### 1. delegation.md · Framing 节（核心改动）

现文保留前两段（goal-not-task / 先开后读并行句——rent 已付）。在并行段
之后接两段新文：

> A whole research goal fits inside one engagement. A colleague given
> the goal — not a leg of it you have already decomposed — runs the
> full loop themselves: understanding, hypothesis, change, measurement,
> verdict, again. Within the charter you handed them, every local
> direction is theirs — what to try next, which of their own hypotheses
> to abandon, when to report rather than grind. Changing the charter
> itself — which basin the program works, whether the framing is tired,
> what counts as a conclusion — is reserved to you, and is exercised at
> junctions, not along the stretch.
>
> The time box on an engagement is a fuse, not a budget. It bounds how
> long a colleague may run before someone looks for them; it never
> sizes the work you may hand over. A speculative seat can carry a
> short box — a dead end should cost its box, not your attention — but
> the mainline carries the whole goal under an open box, carried
> forward by continuation. A brief written to fit a box has already
> been cut into a task; hand the goal.

**租金论证**：第一段同时落边界修正①（段内方向归 executor / charter 级归
PI——Cb 证明现状缺的是前半句，"optimize the binary search 是一个 career"
的分解冲动由它解除）与 delta 2 的简报形状。第二段直击 Cb 读数的原句
（"the brief I write has to be completable inside the box"），fuse/budget
的对偶是可被模型复述的记忆点；保留投机短盒的正当性（stop-loss 价值不丢）。

### 2. native_tools.py · timeout_minutes 描述（×5 处，抽共享常量）

现状（五处重复）："engagement time box in minutes; when omitted the role
default applies (searcher 60, executor 120, proposer/challenger/reviewer 180)"

改为共享常量 `_BOX_PARAM_DESC`：

> "how long this engagement may run before it is salvaged — the
> colleague's report, transcript, and session survive, and a salvaged
> executor can be continued; it guards against a colleague running
> unwatched, it does not size the work. Omitted: role default
> (searcher 60, executor 120, proposer/challenger/reviewer 180)."

**租金论证**：这是 PI 读 box 语义的第一现场（Cb 的"预算"读法源头之一）。
全部是机制事实（超盒→打捞→可续），零立法。顺手消五处重复——先例
_box_from_action 的"one shared path"。

### 3. native_tools.py · executor 描述（一处）

现句 "...they own how the work is carried through." 尾接：

> "...they own how the work is carried through — and a whole research
> goal can be the engagement: the loop of hypothesis, change,
> measurement, and verdict runs inside their stretch, not your
> decomposition."

**租金论证**：派遣形状在工具描述层的锚点；与 §二.1 呼应（常驻层一句、
技能层一段，同义不同密度）。

### 4. native_tools.py · NATIVE_RUNTIME_BLOCK 身份宣言（一句）

在现有三条 identity 论述之后加第四条：

> And the division of the work is fixed: colleagues own the stretches
> — the time a running engagement spends is yours to think in — while
> you own the junctions, where evidence lands and the program turns.

**租金论证**：stretches/junctions 是本轮身份的总纲，值得常驻层一字；
与既有第一论（"the time they spend is yours to think in"）缝合成同一呼吸，
不另起炉灶。约 +28 词。

### 5. native_tools.py · revise_research_state 描述（态势升级）

现句 "Rewrite your Current Research View — the one page of how you
understand the problem now — at a real research junction..."

改为：

> "Rewrite your Current Research View — the one page of where the
> research stands: what you believe about the problem, which lines
> are still paying, the decisive uncertainty, and whether the framing
> itself is tiring — at a real research junction..."

**租金论证**：定稿的 View 语义升级（信念态→态势态）落在 PI 写 View 的
第一现场。为什么放工具描述而非宪章：写 View 的时刻就是读这段的时刻。

### 6. prompts/research_memory.md · 两句

View 段（"It is not an instruction and it is not established fact."后）：

> It answers where the research stands — which lines still pay, the
> decisive uncertainty, whether the framing is tiring — not what to
> do next.

Memory 长期段（"What lives there does not need to stay in attention to
stay alive." 后）：

> Its record is why the research changed its mind — never a backlog
> of what to try next; each entry is your own distillation of the
> evidence, not a forwarding of a report.

**租金论证**：第一句 View 升级的常驻侧。第二句一句双护栏——why-changed-
mind 定性 + 防退化 2（memory 亲手策展，r5 二手转述风险的解药）。

### 7. delegation.md · Watching 节（读世界义务，防退化 1）

现有 "Watching is free: wait in bounded slices, and between them read
what the colleague has laid down — the transcript the acknowledgment
points to, the diff in the tree, the gates they ran." 尾接：

> Read the tree as seriously as the transcript: the diff and the code
> under it are the world itself. A junction diagnosis you cannot
> ground in code you have been reading is an opinion about reports,
> not about the research.

**租金论证**：r5 双向证据（判读好时读 diff；假地板时只信收敛）→ 这句
把好路径写成默认。不立法（不说"必须"），说因果（"cannot ground... is an
opinion"）。

### 8. delegation.md · re-chartering 节（外部视角优先，delta 3）

现有 "When nudges stop landing, re-charter at the leg boundary.
Diagnose before redirecting: ..." 段后接：

> A stuck report is evidence the process needs diagnosis, not an
> automatic request for your next instruction. When you cannot
> confidently say why it stuck, buy epistemic independence before
> craft continuity: a Challenger on the claim or an open Proposer —
> from outside the context that ran out of ideas — before continuing
> the veteran who holds it.

**租金论证**：定稿裁决四的原文压缩；r5 假地板的直接解药。首句是提案
agent 那句 "Stuck is evidence..." 的定稿版（思考工具、非 taxonomy——本句
不列举卡法种类，守住了不立法裁决）。

## 三、验证阶梯（便宜的先走）

1. **探针 A/B（先于一切花钱）**：文本 v4 落地后重跑 interview 三点，
   通过标准与 v3 实测对照：
   - **D**（首触派遣）：首个动作或近首动作出现 executor 派遣（v3：先读
     一小时）；
   - **E**（链式交接）：≤2 跳内开席位，brief 含整个 goal 与买大的盒
     （v3：5 跳纯勘察未开席）；
   - **Cb**（box 语义）：复述出保险丝语义，不再出现"简报须盒内可完成"
     （v3：budgeted bet 原话）。
   探针不过 → 迭代文本，不进 r6。这是 interview 纪律的用法：说的读数
   归 interview，做的读数归 demo（r6）。
2. **全套 pytest**：预期无文本锚定断言（已核），跑一遍确认。
3. **r6 活跑**（§四）。

## 四、r6 配置

- **run 目录**：runs/singlenode/omilrec-v100-r6-scientist。
- **spec.json**：克隆 r5 形状，改三处——base_sha 回真基线（8bbf2f5，
  omilrec v1.0.0 baseline）、无 relay 种子（世界从基线铺，research memory
  空）、charter 走语义文件（v4 文本）。budget/模型同 r5。
  seat_timeout_max_minutes 保持 480（满盒 8h 够 5h 拉伸，无需动）。
- **发射**：supervisor 配方照旧（ds 环境导出 + setsid nohup 脱离会话），
  node1 空闲可用。
- **图与读数**：plot_omilrec_perf.py 自动纳入新 run；r6 起有 wire ts，
  有效工时是算术。四读数（定稿 §八）：穿线时刻（预测 ≤8h，不修 12h+ 的
  对照已由 v3 文本承担）、破 240、junction 视角（开的是 scope=open 外部
   还是同遗产续挖）、无回归（老兵续用/fork→apply→续主力/memory 避死巷/
  收笔回望）。

## 五、判负与回滚

- 探针不 shift → 文本不是这根杠杆，升级给用户（备选：冷启动一句可发现性
  提示——D 行为的 deferred 选项）。
- r6 四读数不达 → 按定稿 §八砍线条款执行：短窗速度归单上下文，scientist
  退守深任务/长程岗，本设计归档为已证伪假设。

## 六、实施顺序

delegation.md（§二.1/7/8）→ native_tools.py（§二.2/3/4/5，含共享常量
小重构）→ research_memory.md（§二.6）→ pytest → 探针 A/B 三点 →
（用户过目草句）→ r6 发射决定。
