# scientist 身份转变实现设计：v4 文本与 r6 验收

2026-09-01。上游共识：scientist身份转变定稿：三层责任结构.md（423b39b）。
本文是实现设计：落点、逐句草案、验证阶梯、r6 配置。专家送审搁置，
草句的仲裁人改为用户本人——文学标准不变（描写不立法、自洽、像人话）。

**审计轮 2（同日，审计方读完整上下文后）已并入**：⑤Research Team 的
Executor 常驻定义仍停在旧模型——首句 "turning a research idea into an
actual experiment or implementation" 就是 idea→implementation 形状；若只改
工具描述不改常驻角色定义，模型会把整目标从句读成特殊用法而非新身份。
research_team.md 从零改动名单移出，Executor 首句升级为 stretch owner。
⑥runtime block 身份句避免 "fixed"（法条味），改用描写性动词（holds）。
落定后的分层：Charter 定义"我为何存在"（不动）→ Research Team 定义"每个
人是谁"（Executor=stretch owner）→ Cold Start 定义"开局如何进入工作形态"
（先开后读）→ Delegation 定义"具体怎么做"（整目标/fuse/watching/junction）
→ Tool descriptions 在调用现场再次激活语义。没有任何一层互相打架。

**审计轮（同日）已并入**：①"先开后读"的 discoverability 提前到冷启动第一
现场（agent.py `_COLD_START`，scientist.md 核心仍零改动）——理由：delegation
是按需技能，而要修的失败发生在首次 delegation 之前，存在"先决定委托→才加载
委托技能"的因果循环；且实测加重证据：现行许可式句子 "you may open them
before any stable judgment exists" 正是 D 探针读出"先读一小时"时在场的文本。
②删除 "open box"（机制上不存在，schema 上限 480；正交性：fuse 界定无人
看管区间，continuation 延长的是所有权不是范围）。③读世界义务从"code"
泛化为"decisive live evidence"（junction 不全是代码级：方法论/objective/
文献/噪声皆可能）。④r6 验收拆成两个证伪目标（行为读数=文本是否推得动
行为；性能读数=新架构是否带来增益）。

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
| scientist/agent.py（`_COLD_START`） | 一处从句级改写：许可式"may open before any stable judgment"→非门控+并行陈述 | delta 1（审计点①：打断"先理解才委托"的启动循环） |
| scientist/research_skills/delegation.md | Framing 节新增两段 + Watching 增句 + re-chartering 增段 | delta 1/2/4、防退化 1、边界修正 ①② |
| scientist/native_tools.py | ① timeout 描述×5 处换保险丝语义（抽共享常量）② executor 描述加整目标从句 ③ revise_research_state 描述升态势 ④ NATIVE_RUNTIME_BLOCK 加一句身份宣言 | delta 2、View 升级、层级宣言 |
| scientist/prompts/research_memory.md | View 段加态势定义句；Memory 段加 why-changed-mind 句 | View/Memory 语义升级、防退化 2 |
| scientist/prompts/research_team.md | Executor 常驻定义首句升级：idea→implementation 改为 stretch owner（审计轮 2-⑤） | 新身份的常驻锚点 |
| scientist/prompts/scientist.md | **零改动**（见总原则 2；两轮审计确认保留） | —— |

工具面其余零改动：challenger 描述已含 "attack the current judgment"（覆盖
防退化 3 的靶子）；wait/cancel/continue 描述已是事实化动词；STUCK 自报与
handover 的 dead_ends/open_questions 结构已存在（演绎里的 stuck 账本有承载）。

## 二、逐句草案

### 0. agent.py · `_COLD_START`（审计点①：冷启动第一现场）

现句（许可式，D 探针证明打不断启动循环）：

> "...are work for Searcher, Proposer, Executor, Challenger, or
> Reviewer, and you may open them before any stable judgment exists."

改为（非门控 + 并行）：

> "...are work for Searcher, Proposer, Executor, Challenger, or
> Reviewer — and for work a colleague can own, their start is not
> queued behind your understanding: open the engagement, and let
> your reading of the terrain catch up in parallel."

**租金论证**：delta 1 的第一现场。delegation.md 教得再好，加载它的前提
是已决定委托；要修的失败恰恰发生在决定之前。此句是陈述不是指令（说
世界的关系，不说"你必须"）。注意与首句 "Ground yourself in the live
world" 的张力由审计点③的口径解决：grounding 保留，只是不再把门——
分钟级定向（objective/gate/目录/活世界确认）之后即可派遣。

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
> short fuse — a dead end should cost its box, not your attention —
> while the mainline carries the whole goal, however many hours that
> turns out to be: a fuse ends an unattended interval, and
> continuation hands the same goal back to the same colleague —
> ownership extends, the goal is never cut. A brief written to fit a
> fuse has already been cut into a task; hand the goal.

**租金论证**：第一段同时落边界修正①（段内方向归 executor / charter 级归
PI——Cb 证明现状缺的是前半句，"optimize the binary search 是一个 career"
的分解冲动由它解除）与 delta 2 的简报形状。第二段直击 Cb 读数的原句
（"the brief I write has to be completable inside the box"），fuse/budget
的对偶是可被模型复述的记忆点；保留投机短盒的正当性（stop-loss 价值不丢）。
**审计点②已并**：原稿 "under an open box" 删除——机制上不存在无限盒
（schema 上限 480），whole-goal 与 fuse 长度正交：20 小时的目标 =
480 fuse → salvage → continue → ……，目标从未被切。买多大的 fuse 是
unattended-interval 的工作负载决定，不是整目标委托的语义条件。

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

> Read what the world holds as seriously as the transcript: the diff,
> the code under it, the gate logs, a rerun of the measurement. A
> junction diagnosis grounded only in reports is an opinion about
> testimony, not about the research — touch the decisive evidence
> itself, whatever it turns out to be.

**租金论证**：r5 双向证据（判读好时读 diff；假地板时只信收敛）→ 这句
把好路径写成默认。不立法（不说"必须"），说因果（"grounded only in
reports is an opinion"）。**审计点③已并**：原稿把落点写死为 "code"——
但 junction 不全是代码级（benchmark 方法论、objective framing、文献突破、
测量噪声、实验设计系统性偏差皆可能），泛化为 decisive evidence，OMILREC
语境下通常恰好是 diff/code/gate/profile，枚举作例子不作边界。

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

## 三、验证阶梯（便宜的先走）——v4 实测结果（2026-09-01）

**pytest：168 全绿**；渲染常驻 2525 词（v3 2401），v4 标记齐、旧标记
（budget 措辞/idea→implementation/Three things）全消。

探针 A/B 实测：

- **Cb：过关（决定性）**。语义整体翻面，原话对照——
  v3："The box is a budgeted bet… the brief I write has to be completable
  inside the box… 'optimize the binary search' 是一个 career"；
  v4："It is a **stop-loss on an unwatched seat, not a target size for
  the work**… Work is sized by scientific increments (hypothesis →
  verdict, **or one whole goal**)… it doesn't size any individual task…
  I would **not** tell a colleague 'fill the box'——volume, not
  verdicts"。先决条件（box 语义）确认修复。
- **D（单调用）：过关形状**。首跳三读全是定向级（ls/git log/status、
  README 头、目录清点），无 profiling、无源码通读、无先形成假设。
- **链式派遣观察：未取得（诚实记录）**。五次链式尝试（E/D2/Bc/Bc2/G）
  全部被探针自身保真缺口消耗跳数或烧毁上下文：假 shell 被识破 ×2、
  卡世矛盾 ×2（"新任务"卡对收尾态世界；"58 条"卡对空 memory 通道）、
  /work 路径发现耗 2-3 跳、memory 列表 15 条截断逼它手读 JSONL。
  每次深挖都是对我探针缺陷的合理反应，不是委托回避；但也无法据此
  宣称"≤2 跳派遣"过关。**派遣形状（整目标简报+自选 fuse+workspace）
  的真实读数属于 r6 的 wire**——第一个 dispatch 会被逐字记录。
- 探针方法论沉淀：链式 interview 要世界全通道真实才不烧（Bc2 的
  教训写进了 probe 脚本注释）；这正是"说的读数归 interview，做的
  读数归 demo"的边界案例——派遣是有赌注的行为，本来就属于 demo。

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

## 五、判负与回滚（审计点④：两个证伪目标分开）

r6 的读数分两类，判负含义不同：

**行为读数（身份假设的直接检验）**：先开后读实测（无 substantial
pre-delegation investigation）、整目标简报、stretch 自持（continue 链
长度、无段间上手）、junction 外部视角、读世界行为、View/Memory 新语义。
**不 shift → 文本实现失败**——迭代文本重走阶梯，与身份假设对错无关。

**性能读数（更强假设的检验）**：穿线时刻（≤8h 预测）、破 240、终值、
token/wall。**行为 shift 而性能平于 coding 臂 → "scientist orchestration
在此任务/预算下提供额外增益"被证伪**——身份实现本身成立，按定稿 §八
砍线条款执行（短窗速度归单上下文，scientist 退守深任务/长程岗），paper
里两个结论分开写。

探针不 shift → 文本不是这根杠杆，升级给用户（冷启动已按审计点①提前，
下一级备选是模型侧而非文本侧）。

## 六、实施顺序

agent.py `_COLD_START`（§二.0）→ delegation.md（§二.1/7/8）→
native_tools.py（§二.2/3/4/5，含共享常量小重构）→ research_memory.md
（§二.6）→ pytest → 探针 A/B 三点 → r6 发射决定（用户）。
