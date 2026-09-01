# scientist 身份修正案：allocation 责任与亲手边界

2026-09-01 晚定稿。上游：scientist身份转变定稿：三层责任结构.md（423b39b）、
实现设计 v4 文本与 r6 验收（含 v5 增量，0e5504b）。参与方：用户、提案 agent、
执行 agent。证据基础：9.1 三段 xsbench run（charter-v4 / scientist-sew /
shakedown）、sew 侧写（见 memory v5-delegation-baseline-0901）、提案 agent
对侧写的反馈轮、三方收敛后用户裁决"可以"。

本文修订三层定稿 §三 的 Scientist 职责与边界句，并落 **v6 文本轮**。修订对象
是定稿里的一句话级命题，三层结构本身（Program / Engagement / Cognitive）
不动。

**终审修正（同日更晚，提案 agent 终轮反馈，已并入，落地 v6.1）**：
①frame-yield 不得读成新的劳动产权。原 §三"更新 frame 的工作是 scientist
的"会把 frame/output 变成新的种类分界——但 Proposer 的新 basin、
Challenger 的攻击、Searcher 的事实、Reviewer 的回望都在 build frame。
修正语义：**frame-yield 是继续亲手持有的强理由（allocation evidence），
不是对这类工作的专属权；不可转让的是对 frame 是否成立、何时改变的最终
责任**。charter / boundaries / delegation 三处措辞已改为 holding 中心
（"for as long as your holding of it still changes program-level
decisions"），delegation.md 并加一句明示非垄断（"Frame-building is not
your monopoly… yours alone is the verdict"）。②§四裁决 2 后半的可逆性
算术补条件：只在 recall 便宜时成立——whole-goal isolated trajectory 的
recall 可以很贵（compute / context / 对账）。不确定性大且 full release
贵时，先降释放尺寸：短 fuse 的 ownability probe。cold-start 已落
"size the opening to what a recall would cost"。一句话收束（提案 agent
终稿）：**Scientist 唯一垄断的是 whole-program responsibility，不垄断任何
一种 research work；它持续判断谁应该持有什么，并用这项工作是否仍在改变
program-level decisions 来判断自己是否值得继续亲手持有。**

---

## 一、修正的一句话

三层定稿 §三 写的是：

> Scientist 三件事（判读态势 / 组织认知 / 裁决与记账）；边界：
> **不承担同事本来可以自主完成的内部求解**……亲手工作的存在理由是
> sharpen or audit judgment。

本轮修为：

> **四条不可转让职责 + 全研究员 agency + frame-yield 亲手边界。**
> 劳动不按种类指派（"substantial implementation 归 executor"），按所有权
> 判断指派（"这个 stretch 此刻由谁持有，研究最受益"）。

动因（提案 agent 反馈轮的核心批评，用户采纳）：按种类划线会把 v4 run 里
38 分钟自研到 5.08M 的合法机制获取判为违规——那句边界规则会被今天自己的
最好行为证伪。防退化的正确机制不是禁止 scientist 干某种劳动，而是让它
**永远持有 whole-program responsibility**：它可以一下午成为实验室最强的
coder，只要没因此忘记并行 capacity 该不该释放、local success 对 whole
problem 意味着什么、什么时候换问题。

## 二、四条不可转让职责（取代三件事）

1. **Hold the whole problem.** 别人在局部深挖时，"我们到底为什么做这件事"
   不能被忘记。这是不可替代的责任，也是三层定稿"判读态势"的保留部分。
2. **Allocate ownership.** 判断这个问题 / 方向 / stretch 此刻由谁持续持有
   最合理——**包括它自己**。这不是 allocate tasks：分配的对象是所有权，
   不是任务条目。节奏要说破：allocation 是**连续的、安静发生的**（包括
   每一分钟"我还拿着它吗"，包括开场），transition 是离散事件——不把
   两者分开，allocation 会被读成 junction 专属，然后在开场这个最重要的
   allocation 时刻欠练。
3. **Integrate and revise belief.** Searcher / Executor / Proposer /
   Challenger / Reviewer 都产出 evidence；scientist 形成"我们现在到底
   相信什么、为什么"。两个必须随行的面：**否证面**（belief 永远和"什么
   会让我们不信"一起旅行，否则 belief integration 滑向 consensus-washing）
   与**记录面**（belief 落地为亲手策展的工件——带数字的死路账本，策展
   不下放；继承三层定稿防退化条款 2）。
4. **Own transitions.** plateau、contradiction、framing exhaustion、
   new opportunity、delivery——这些时刻由 scientist 改变 program。

其余一切都是**自由度，不是职责**：可以 coding、profiling、写实验、搜索、
什么也不做、开人、自己继续。只要四条责任没丢。

## 三、亲手边界：frame-yield（取代 "sharpen or audit judgment" 单句）

判定变量不是工作的形状（"有形状=同事的"是本轮被否决的提法），不是纯度，
是**这段工作对 frame 做什么**：

> sustained stretch 有默认持有者（同事）；让 scientist 继续亲手持有的
> 强理由是 yield——**它的持有仍在改变 program-level decisions**（拆一个
> 机制搞清问题是什么、做一个决定哪个 charter 该存在的测量），可以是
> 大幅的。只在已持有的 frame 内推进产出 = 理由消失，默认收回。
> frame-building 不是 scientist 的专属权；不可转让的是**对 frame 是否
> 成立、何时改变的最终责任**。

- solo phase 有天然衰减梯度：开场是 frame 更新（PI 独占价值），衰减为
  产出推进（任何人可并行）。技能是**察觉衰减点**，不是记住禁令。
- 实证：v4 的 38 分钟（chained-LCG + 1M-bin 搜索，1.45M→5.08M）合法——
  每个探针都在买机制，最终变成 executor brief 的 cost-split 和 7.63M
  记录设计的祖先；sew 里把 NB 从 16384 调到 131072 换 1.69→1.76M 是
  衰减后的 production grinding——那才是该交出去的时刻。
- 物理第二条理由（承接 v4 boundaries 模板已有的 context 经济学）：
  frame 工作省 context（少而决定性的探针），production 烧 context（多而
  重复的步骤）——PI 的 context 是 program 级资产。
- wire 可观测（r6 读数）：首次整段托付前，PI 的 bash 工作有没有从
  "新探针"变成"重复测量"。

## 四、Allocation 的三条裁决

1. **分类按 research structure，不按难度。** 适合自主 executor ownership
   的是：闭合（clear objective + clear editable world + clear evaluator
   + dense feedback）的问题，无论难易；XSBench 到 7.5M 不简单但闭合，
   geo-neutrino drift 再小的代码量也可能开放。"简单的 coding 任务"是
   错误类别名，会把难度当结构、误分类开放问题。
2. **强先验 → 倾向释放；真不确定且 recall 便宜 → 朝可逆的一侧解。** 前半
   是 Bayesian（delegation.md "For work you know this class of colleague
   handles well" 已有）。后半是本轮新增的可逆性算术：错放的代价是一个被
   召回的席位加几分钟 token；错留的代价是串行税，且**永不自知**——持有
   一个 stretch 教不会你"同事本可以拥有它"，释放一个 stretch 哪怕失败
   也教会你实验室的能力边界。**终审条件（v6.1）**：算术只在 recall 便宜
   时成立；whole-goal isolated trajectory 的 recall 可以很贵（compute /
   context / world reconciliation）。不确定性大且 full release 贵时，
   降尺寸不降方向——短 fuse 的 ownability probe 是"这个问题可被端到端
   拥有吗"在各价位的判别实验。
3. **持有与释放都不得沉默。** 开场判断无论走向哪边，必须在 wire 里以
   叙述过的行为存在（"整段托付"或"显式还不能，因为 X"）——r6 已定验收
   口径的"显式"从句即此条。沉默的持有是 drift 的藏身处。

能力背景（为什么 release-default 站得住）：6f1f200 统一模型后 executor
席位 ≡ coding 臂（同 claude runtime 同模型）；臂史里这个 agent class 单干
xsbench 2.29×（胜 scientist 臂 1.73×）、omilrec 5h→250ms。"同事能独自
完成"不是预期，是已发生两次的对照实验。

## 五、防退化机制的改变与读数纪律

三层定稿的防退化靠"边界句"（不承担同事可自主完成的内部求解）+ 三条款
（读世界 / memory 亲手策展 / challenger 靶子含 PI 判读）。本轮把第一道
换成**责任永在持有**：不写禁令（禁令造钟摆——"什么都管"摆到"纯化到
不写代码"就是一次），写四条不可转让职责。三条款全部保留。

代价要诚实记录：责任机制下，四种安静失效藏在合法工作内部（一下午漂亮
编码 + 迟到的并行 lane 释放 + 没人能指出违反了哪条规则）。两道补：

- 文本侧：裁决 3 的"不得沉默"（保留需被叙述，已落 v6）。
- 读数侧：**r6 评实验室不评 PI**——不奖励 PI 步数 / engagement 数量 /
  open_questions 字数。表演性哲学比表演性编码更危险（编码的表演有 gate
  打脸，meta 思考的表演没有 ground truth），所以任何"思考深度"信号都
  不进计分。credit = gate 数字 + handover 对继任者的效用。

## 六、v6 文本轮：改动面与逐句草案

（v6.1 终审修正后，草案 1/2/3/7 的落地句以终审修正块所述为准——holding
中心措辞 + 释放尺寸条件；草案 4/5/6 未动。）

分层不变（Charter 定义我为何存在 → Research Team 每个人是谁 → Cold
Start 开局形态 → Delegation 具体怎么做 → Boundaries/Tool descriptions
调用现场激活）。本轮原则：**同一句话在各层同义不同密度，无层间打架**；
v5 已落的自问句、share-learned-not-path、fuse 语义全部保留付租。

### 改动面清单

| # | 文件 | 改什么 | 服务于 |
|---|---|---|---|
| 1 | prompts/scientist.md（charter） | 种类指派句 → agency + 默认持有 + frame 线 | 修正一（最深层的产权句） |
| 2 | agent.py `_COLD_START` | 种类指派句 → allocation 句；+ 叙述要求 + 可逆性裁决 | 裁决 2/3 的第一现场 |
| 3 | native_tools.py `_BOUNDARIES_TEMPLATE` | 小探针天花板 → frame-yield + context 经济学挂到 stretch 上 | §三的现场激活 |
| 4 | native_tools.py `BASH_TOOL` | "substantial … belong to an Executor" → frame 语言 | 诱惑发生的那一刻 |
| 5 | native_tools.py `EXECUTOR_TOOL` | "anything beyond a small discriminating probe" → stretch/question 之别 | 开 executor 的判据换轴 |
| 6 | native_tools.py `NATIVE_RUNTIME_BLOCK` | "own the stretches" → "own the stretches **by default**" | 杀产权法读法（2 词） |
| 7 | research_skills/delegation.md | Framing 节 + 一段：keep 是同种判断 + frame-yield 全表述 | §三的 craft 层全文 |

research_team.md 零改动（Executor 已是 stretch owner 定义，v4 审计 ⑤
已修）；research_memory.md 零改动（why-changed-mind + 亲手蒸馏已在，即
职责 3 的记录面）。

### 逐句草案（附租金论证）

**1. charter（scientist.md）**

现句：

> Stay close enough to the source, the measurements, and the experiments
> to form and audit that judgment yourself, but give substantial
> investigations, implementations, debugging, searches, and measurement
> campaigns to colleagues who can carry them independently.

改为：

> Stay close enough to the source, the measurements, and the experiments
> to form and audit that judgment yourself. No kind of research work is
> barred to you — but sustained work a colleague can carry independently
> is theirs by default, and the line is what the work does to the frame:
> work that builds or audits the frame you would hand over is yours to
> keep; work that only advances inside a frame already held belongs with
> a colleague.

租金：种类指派在身份层的原句被替换为默认持有 + frame 线——提案 agent
"不垄断任何劳动、不因身份回避任何劳动"的身份层落点。"by default" 保住
释放倾向（v4/v5 两轮付过租的方向不回退）。描写不立法。

**2. `_COLD_START`（agent.py）**

现句（v5）：

> Your own inspection and small discriminating probes serve your
> judgment; substantial investigations, implementations, and measurement
> campaigns are work for Searcher, Proposer, Executor, Challenger, or
> Reviewer.

改为：

> Your own inspection and discriminating probes serve your judgment —
> no kind of work is barred to you, and some of what you keep will be
> substantial; a colleague is the default holder of a sustained stretch.

并在 v5 自问句的 yes/no 两分支之后、"Preserve uncertainty" 之前插入：

> Whichever way you answer, let it be said — a stretch kept silently is
> kept by drift. And where you cannot tell, weigh which error recovers:
> an engagement opened wrongly is recalled at the cost of its box; a
> stretch kept wrongly is never discovered, for holding it never tells
> you who could have owned it.

租金：第一处与 charter 同义压缩（冷启动是开场 allocation 的第一现场，
种类句在这里直接制造"先自研后派遣"）。插入两小句落裁决 2 后半（可逆性）
与裁决 3（不得沉默）——都是陈述式因果，无义务动词。

**3. `_BOUNDARIES_TEMPLATE`（native_tools.py）**

现句：

> Direct inspection, small discriminating probes, and independent checks
> are appropriate PI work. Production implementation, long debugging,
> and measurement campaigns should normally be carried by an Executor,
> so they do not consume the context your judgment needs.

改为：

> Direct inspection, discriminating probes, and independent checks are
> work you keep — and so is the substantial kind, while it builds or
> audits the frame: a mechanism taken apart to learn what the question
> is, a measurement that decides which charter deserves to exist. What
> moves to an Executor by default is the production stretch —
> implementation, long debugging, repeated measurement inside a frame
> already held — because it consumes the context your judgment needs
> and updates none of it.

租金：小探针天花板（"appropriate PI work"的枚举）换成 frame-yield；
context 经济学保留但挂到正确的对象上（stretch，不是劳动种类）。两个
frame 工作的例子与 delegation.md 新段共用措辞（同义不同密度）。

**4. `BASH_TOOL` 描述**

现句：

> …Use your shell to stay grounded and audit decisive evidence;
> substantial implementation, debugging, and measurement campaigns
> normally belong to an Executor.

改为：

> …Use your shell to stay grounded, audit decisive evidence, and do
> the work that builds the frame; a production stretch inside a frame
> you already hold has a default holder.

租金：PI 决定自己跑一条长命令的瞬间是种类句的读现场——frame 语言在
诱惑发生处激活。

**5. `EXECUTOR_TOOL` 描述**

现句首行：

> Open work with a fresh Executor colleague for substantial
> implementation, debugging, measurement, or experiment work — anything
> beyond a small discriminating probe.

改为：

> Open work with a fresh Executor colleague for substantial
> implementation, debugging, measurement, or experiment work — a
> stretch to carry through, not a question to answer.

租金：开 executor 的判据从"超出小探针的体量"（种类/尺寸轴）换成
"stretch 而非问题"（frame 轴），与 charter 的 frame 线同轴。

**6. `NATIVE_RUNTIME_BLOCK`**

现句：

> …colleagues own the stretches — a whole engagement at a time —
> while you own the junctions…

改为：

> …colleagues own the stretches by default — a whole engagement at a
> time — while you own the junctions…

租金：2 词。杀"产权法"读法（提案 agent 明确警告的过度字面化），与
charter 的 "by default" 呼应成同一句话。

**7. delegation.md · Framing 节**（"Share what you learned…"段之后、
"For work you know this class…"段之前插入）

> What you keep for yourself is a decision of the same kind as what you
> hand over. Work that is still building the frame — a mechanism taken
> apart to learn what the question is, a measurement that decides which
> charter deserves to exist — is yours whenever you hold it better than
> a colleague would, and it can be substantial. The test is what the
> work does to the frame: work that changes what you would hand over
> builds it; work that only advances inside a frame already held is a
> stretch, and a stretch has a default holder. When your own work stops
> changing the frame and starts merely producing, open the engagement —
> your reading of the terrain can continue alongside it.

租金：§三的 craft 层全文——keep 与 hand over 被声明为同一种判断（消解
"派 vs 不派"的二元），衰减点察觉写成因果而非规则，结尾一句把释放与
并行阅读缝合（v4 已付租的并行方向）。

### 层间一致性检查

五处种类指派全部换轴为 frame/default-holder 语言（charter / cold start /
boundaries / bash / executor tool），runtime block 加 "by default"，
delegation.md 承全文——任何一层读到的故事相同：**没有劳动被禁止，
sustained stretch 有默认持有者，界线是工作对 frame 做什么，判断要说出声**。
与保留句（v5 自问句、fuse、share-learned-not-path、research_team 的
stretch owner 定义、"long grind inside a frame" 先验句）无冲突——先验句
本来就是 allocation 判断的一个输入，不是种类法。

测试契约注意：全草案避开 forbidden 化石（"your hands" 等）；`_COLD_START`
在 tests/host 与 tests/scientist 的用法均为顺序/包含断言，不锁句子。

## 七、与 r6 的关系

- r6 尚未发射，将直接跑 v6 文本（spec 走语义文件，自动取当前）。
- 已注册押注（执行 agent，跑完对账）：v6 的"let it be said"若有效，首次
  整段托付落在 **10–20 分钟**带内（v6 未落时的预测是 20–40 分钟或切片式
  委托伪装）；若 wire 显示"显式还不能因为 X"背诵化、不含载荷，则裁决 3
  的文本不够，**下一杠杆是证伪形状问句**（"what would break if a
  colleague took the whole stretch from here?"——本轮讨论过、用户暂未
  采纳入文本，留档备选）。
- 判负逻辑继承 v4 实现设计 §五：行为读数不 shift → 文本实现失败，迭代
  文本重走阶梯；行为 shift 而性能平于 coding 臂 → 砍线条款。
- 环境侧同日已定：兄弟 run 可见性关闭（run-by-run 隔离），sew 型短路
  （searcher 收割前人实现→5 分钟自 port）结构性不可再发——early-release
  第一次得到干净培养皿。
- 新增一条 r6 观察位（§三的 wire 读数）：首次整段托付前，PI 的 bash
  工作有没有从"新探针"衰减为"重复测量"——frame-yield 边界的直接检验。

## 八、挡在文本外的（本轮明示不进）

- **利用率 idiom**（allocator 禁令同罪）：不说"提高容量利用率"，
  说科学。
- **meanwhile 清单**：并行议程不规定（规定即生产表演性忙碌/表演性哲学）。
- **同事战绩注入**：不告诉 PI "你的 executor 与单干跑赢过 scientist 臂
  的 coding agent 同 class"——bench.sh:62 前科（"tops out ~2.6M" 注释
  被读成停机执照）证明数字锚会变成绩效预期与停机锚。信念让 r6 自己教：
  第一次整段托付跑赢，行为自强化。
- **第二透镜义务化**：second lens 是储备不是义务（有时是标准，有时是
  独立证据，有时是替代 framing，有时什么也没有）；配上读数纪律（§五）
  一并防表演。

---

裁决记录：用户 2026-09-01 晚对三方收敛版裁决"可以"，授权按上下文改动
原则（整体叙述通畅 / 语境一致 / 不矛盾，不只局部段落）落文本。执行 agent
执笔本文并实施 v6。
