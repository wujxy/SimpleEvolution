# scientist 身份转变定稿：三层责任结构

2026-09-01 定稿。参与方：用户、提案 agent（docs/chat/2026.9.1.11.40.gpt谈scientist身份转变.md）、
执行 agent（本文执笔）。证据基础：r1/r5 活跑、coding 臂对照、专家线复测、
charter interview 套件（scripts/probe_charter_interview.py 的 D/E/E2/C/T 等读数）。

本文是**身份与职责的共识定稿**，不是文本改写方案。文本层面的落地（宪章、
delegation.md、工具描述）等送审流程（pi-charter-review.html）回来后另行开工。

---

## 一、转变的一句话

旧模型（r1 时代的实际形态）：

> Scientist 主导研究 → 提方案 → Executor 当手脚去实现。

新模型：

> **Executor owns the stretches. Scientist owns the junctions.
> Other seats help the Scientist see the junctions.**

Scientist 不再通过告诉强 agent"该做什么"来领导研究，而是通过维持强 agent
的自主研究、在需要时引入新的认知视角、并在关键转折处形成科学判断来领导研究。

它执掌的是**研究阶段的流向**：研究处在哪一段、这一段缺什么认知、要不要转向。
"怎么做"整体下放。这不是削弱——omilrec 上破地板的能力本来就以 junction 动作
的形态发生（见 §七防退化条款），新身份是把它从偶发好行为升格为定义。

## 二、等级：一层责任差，不是智力差

只有两层责任、三层结构：

```
PROGRAM LEVEL
Scientist
owns: goal / research regime / orchestration /
      charter transitions / scientific judgment / delivery

        ↓ charter          ↑ evidence
ENGAGEMENT LEVEL
Executor
owns: autonomous research inside the charter

        ↑ new perspectives
COGNITIVE SEATS
Proposer / Challenger / Reviewer / Searcher
own: independent ways of seeing the research
```

- 没有谁比谁聪明，只有**责任尺度不同**。Scientist 居中不是因为它更会研究，
  而是因为只有它承担跨 engagement 的连续责任和最终裁决。
- 认知席位相互完全横向。Executor 是唯一改世界的席位，但不因此指挥别人。
- 实现层印证：**executor 单独成层的真实标记是它是唯一可续的席位**
  （continue_engagement 仅 executor；认知席位每次 fresh open，是刻意无状态的）。
  持续的世界改变者 vs 刻意无状态的看的方式——三层不是概念图，长在机器里。

## 三、各席位职责定稿

### Scientist —— 课题的执掌人（program holder）

三件事：

1. **判读态势**：研究处于什么 regime——产油 / 变窄 / 枯竭 / framing 疲劳。
   信号在系统里：棘轮趋势、假设多样性（transcript 里新想法是否全是已入账
   想法的变体）、死巷密度、单位工时收益。
2. **组织认知**：此刻缺什么就开什么——executor 腿、challenger 攻某个信念、
   proposer 扩空间、searcher 查事实、reviewer 回望。开/续/杀，box 是保险丝
   不是预算。
3. **裁决与记账**：证据意味着什么、什么入账（apply + 重验）、View 说什么、
   Memory 记什么、何时交付。

边界：**不承担同事本来可以自主完成的内部求解。**它可以判断 mechanism、提出
discriminating hypothesis、指出新 framing——但不把 implementation path、函数
修改、debugging plan 替 executor 做完。亲手工作的存在理由是 sharpen or audit
judgment：grep、看 diff、重跑一次测量、写小的判别探针。

它不需要比 Executor 更早知道答案。

### Executor —— charter 内的自主研究主体（stretch owner）

拿**整个研究目标**（如"gates 全 PASS 下把 SPEED_MS 尽可能压低"），自持完整循环：

```
inspect → understand → profile → hypothesize → implement
       → measure → revise → continue
```

只要还在有效推进就继续；卡住时**报 stuck 带账本**（做了什么、认为哪些轴
关死、剩余想法清单），而不是硬磨。工件随做随 commit。

两条边界（本次定稿的关键修正）：

- **Executor owns local scientific direction inside the charter.
  Scientist owns changes to the charter itself.** 段内方向（下一个试什么机制、
  放弃哪条局部假设、这条路没用了换另一条）是 executor 的；换 basin、reframe、
  换主线、什么算研究结论，是 Scientist 的。注意：一边说"整目标自持"一边写
  "不决定研究方向"，模型会退化回"等 PI 给 proposal"。
- **不单方面改变研究 charter，也不把自己的局部判断当成 program-level
  conclusion。**"250 就是 floor"是 testimony，不是结论——r5 的假地板教训。

### Proposer —— 搜索空间扩展器

mainline 变窄或 framing 疲劳时提供新机制、新解释、framing 外的机会。
scope=open **不继承 Scientist 当前判断**——否则只是帮 mainline 多想两个
variation。它的价值不是 backlog，是问"有没有另一个值得存在的 basin"。

### Challenger —— 信念的攻击者

攻击当前 bottleneck 判断、research framing、**Scientist 已形成的解释（含
regime 判读本身）**、Executor 的隐含假设、看似成立的结论（含 floor 主张）。
与 Proposer 的分工：Proposer 问"还有什么可能是真的"，Challenger 问"我们现在
相信的为什么可能是假的"。每个攻击带证据形态，不为反对而反对。

### Reviewer —— 长时间尺度的观察者

价值来自距离。检查：叙事与实验记录是否一致、是否绕同一盆地打转、被遗忘的
早期信号、plateau 是否早有前兆。三个自然时机：大战役后、疑向时、收笔前。
Advisory only——这是 Scientist 侧的聆听义务，不是 Reviewer 的权力。

### Searcher —— 事实接口

文献、precedent、外部实现、代码事实。把外部世界变成可靠 evidence，不判断
"因此该做什么"。Scientist 的习惯应该是"我不知道这个事实"→ Searcher，
不是自己花一晚上 context 查。

### 两张账本（与席位并列的数据结构）

- **Research View = 态势页**：研究现在站在哪——哪条线还在产、决定性不确定度
  是什么、framing 疲不疲劳。（升级：从信念态描述升为 regime 仪表盘。）
- **Research Memory = 研究为什么改变了想法**：死轴的准确适用范围、转折的
  证据、什么 framing 转向了、orchestration 经验何时有效/失效。（升级：不再
  服务于 PI 的方案连续性，服务于整个 multi-agent 研究过程的认知连续性。）

## 四、四条工作原则（scale-neutral，从 omilrec 案例提炼）

1. **Autonomy before understanding.** 当同事已被证明能拥有这类问题时，
   Scientist 的理解不构成开工的前置条件——先发，理解并行追上。
2. **Continuity while productive.** 自主 engagement 仍在产生有效进展，就让
   它积累 craft 和 context。
3. **External cognition at junctions.** genuine plateau 出现时，不默认让
   同一上下文再挤一个 idea，优先考虑独立视角。
4. **Judgment remains centralized.** 所有席位都能做科学推理，但 program 级
   的解释、研究转折和最终结论归 Scientist。

## 五、三条防退化条款

身份转变最怕丢掉的两样东西——omilrec 上展露的**破地板自主性**与**研究经验
的发现性**——不住在被搬走的"PI 亲手实现"里（r1 账本证明那是净负债），但有三
条真实的退化路径会伤到它们。护栏：

1. **读世界义务。**自由段的 watching 里，树与代码和报告同权重：diff、commit
   流、关键机制的源码。junction 诊断必须踩在内容上——r5 假地板的成因正是
   只信群体收敛、没人摸机制；r5 判读好的时刻（fork 赢的识别）靠的是读 diff。
2. **Memory 亲手策展。**新形态下经验的生产者是 executor；Memory 条目必须由
   Scientist 从证据（commit、note、handover 的 dead_ends/open_questions）
   亲手蒸馏，不许变成报告的二手转述。策展权不下放。
3. **Challenger 的靶子包括 Scientist 自己的判读。**"我认为这是盆地枯竭"和
   executor 的"250 是 floor"一样是证词。群体收敛不是判读。

## 六、关键裁决记录

- **主线 current / 投机 isolated，语义化**：current = mainline ownership
  （接受态 → executor → commit → 新接受态 → continue，craft 与代码状态连成
  真正的连续研究）；isolated = alternative world（独立激进机制、fresh
  executor、可能大破坏结构的试验、明确的 fork）。不是安全旋钮，是所有权的
  语义。
- **投机席位无配额**："自由段默认开 0~1 个"这类数字不进设计。有独立认知问题
  就开，没有就让 mainline 跑——seats are opened because a hypothesis
  deserves them, never to fill them。
- **stuck 不设固定 taxonomy**：盆地枯竭/技能缺口/framing 错误是思考工具，
  不是世界只有这三种 stuck（还有证据不足、噪声太大、环境限制、两解释不可分、
  objective 内在张力）。核心句：
  **Stuck is evidence that the current research process needs diagnosis,
  not an automatic request for another instruction.**
  真正 stuck 且 Scientist 自己诊断不确定时，**外部视角优先**——continue
  老兵买到的是 craft continuity，open Proposer/Challenger 买到的是 epistemic
  independence；在"我不知道这是不是假 floor"的状态下，缺的显然是后者。
- **亲手边界用描写不用禁令**：不写"大段实现禁止"，写 sustained world-changing
  work belongs with a colleague who can own the whole loop; Scientist's
  direct work exists to sharpen or audit judgment——特殊情形下模型保留判断空间。

## 七、验证场景：omilrec 演绎（场景，非身份）

以下是我们预计在 omilrec 从零 run 上发生的轨迹，**不是** Scientist 的普遍
工作法。时间戳、盒长、席位数都不回写进身份定义。

- **T+0~10min**：冷启动分钟级看 baseline/eval/目录 → **先发 executor#1**
  （整目标、current、满盒、DoD=持续下降或带证据的 stuck 报告）→ 然后并行
  细读代码攒地形感。
- **T+10min~5h 自由推进段**：executor 自持循环（coding 臂证明此弧 5h 到
  ~250）。PI 在 wait 切片间读 transcript/gate/commit 流——不 nudge（无证据
  触发）。投机席位按需（预期 0~1，无配额）。
- **T≈5h 第一面墙**：executor 报 stuck 带账本。PI 三步：读完整轨迹 →
  判读卡的性质（思考工具：枯竭/技能缺口/framing）→ 按判读组织（framing
  嫌疑 → Challenger 攻"为什么默认让每次 FCN 更快" + open Proposer；
  技能缺口 → continue 老兵带诊断；真枯竭 → 续挖或转攻）。
- **破壁段**：新 framing 进来 → continue 老兵（craft + 新方向，r5 验证的
  赢法）或 fresh executor。推进重新自持，PI 只在下一面墙重演三步。
- **收笔**：判读边际价值 < 交付价值 → Reviewer 回望（义务非闸门）→
  deliver_world → Memory 落 why-changed-mind 条目。

**共识行为 vs 当前预期行为的四条 delta**（interview 实测）：

| # | 共识行为 | 当前实测（读数来源） |
|---|---|---|
| 1 | 先开后读 | 先读一小时再开门（D/E ×5） |
| 2 | 整目标满盒 | 盒内可完成的段——"optimize the binary search 是一个 career"（Cb） |
| 3 | stuck 判读开外部视角 | r5 实况：同遗产续挖，收敛出假地板 |
| 4 | 自由段不插手 | 段间上手惯性（Cb 的预算语义所致） |

前两条是速度的钥匙，第三条是破 240 的钥匙，第四条是前两条的自然结果。
第 2 条的先决是 **box 语义改写**（保险丝/止损，不是可 charter 的工作尺寸）。

## 八、验收（r6，从零 omilrec）

图上画死 coding 臂参考线，读四个数：

1. **穿线时刻**：有效工时到 250 的时刻（预测：文本修完 ≤8h；不修 12h+）。
2. **破 240 了吗**：结合体成立的判据——coding 到 240 停，我们复刻其速度后
   由流向判断推过去。
3. **junction 视角**：墙出现时开的是 scope=open 外部视角，还是同上下文续挖。
4. **无回归**：r5 已验证的好行为（老兵续用、fork→apply→续主力、memory
   避死巷、收笔回望）不因身份转变消失——防退化条款的实测。

若修完文本仍 12h+ 到 250 或破不了 240：按约砍——短窗速度属于单上下文，
scientist 退守深任务/长程岗。

## 九、与现有文本的关系

概念层大多已在现行包里（宪章第一段的流向控制定义句、delegation.md 的保留
权清单与并行句、executor 工具描述的 they own how、charter 的 report-as-
testimony）。待文本化的增量，全部等专家送审回来后一并做：

1. box 保险丝语义句（先决）+ 整目标托付句 → delegation.md Framing 节
2. 层级宣言（direction vs way）→ 宪章或 delegation.md
3. View 态势定义句 + Memory "why changed mind" 句 → 各自语义段
4. 三条防退化条款的对应句（读世界义务已在 delegation.md 有原型）

falsification 义务：每句进常驻前按租金标准过——删掉它行为不变，它就不该在。
