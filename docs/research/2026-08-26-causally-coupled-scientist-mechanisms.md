# Causally Coupled Scientist：从 Task Agent 到持续科研主体的机制调研

> **已被后续调研取代。** 本文把行为定义过早翻译成 Prompt / Context / Tool / Harness 组件，并错误扩大了 harness 的认知职责；不应再作为实现基线。请改读 [从 Task Solver 到 Agenda Owner：Scientist 机制调研与设计约束](./2026-08-26-agenda-ownership-mechanism-review.md)。
>
> 状态：已废弃，仅保留讨论历史  
> 日期：2026-08-26  
> 范围：一个持续存在的 Scientist、一个持续演化的 World；暂不讨论多席位、树搜索与演化框架

## 0. 执行摘要

这轮调研得到的首要结论是：**不存在一条足以把基础 Agent 变成 Scientist 的“科学家提示词”**。目前相对可信的路径是一个分层机制：

\[
\text{Scientist Policy}
=
\text{Prompt Elicitation}
\times
\text{Context/Tool/Harness Stabilization}
\times
\text{Behavioral Verification}
\times
\text{Training/Internalization（必要时）}
\]

- **Prompt** 能唤起角色、责任和推理倾向，但单靠角色描述通常只能改变回答风格，不能保证长期身份与证据约束。
- **Context、Tools 与 Harness** 必须使 Scientist 的当前理解实际控制下一步行动，并使现实证据有能力改变其理解。它们不是科研表格，而是科研因果关系的承载物。
- **Behavioral Eval** 不能检查它是否“说了 hypothesis / reflection”，而要改变证据，观察它的理解、问题和行动是否随证据内容发生有意义的变化。
- 如果在相同 scaffold 下，因果失败仍主要由基座模型决定，就需要考虑模型选择、微调或其他内部化手段，而不是继续堆 Prompt 字段。

因此，本项目现在最重要的转变不是让 Scientist “更持久地做用户任务”，而是把控制中心从：

> 用户任务 → 执行 → 达标 → 交付

迁移为：

> Research Agenda → 当前理解 → 值得回答的问题 → 接触世界 → 证据 → 修正理解 → 重构问题前沿

一次优化成功、一次实验结束、一个问题被回答，都是这个循环中的**研究贡献**，不是课题天然终点。

## 1. 本调研冻结的研究对象

### 1.1 Scientist 的工作定义

Scientist 是一个持续拥有 Research Agenda 的认知主体。它根据对世界的当前理解维护动态的 Inquiry Frontier，从中自主选择值得回答的问题，并通过工具接触现实、取得证据；证据能够改变其理解，新的理解又会重构问题前沿。

它同时追求两种进展：

\[
\text{Research Progress}
=
\text{Epistemic Progress}
+
\text{Real-world Progress}
\]

例如在 XSBench 中，识别 scattered memory access 是当前瓶颈属于认识进展；设计新数据布局并验证吞吐提升属于现实贡献。后者只有进入“为什么成功、旧判断改变了什么、新瓶颈是什么”的认识闭环，才是科研进展，而不只是一次 optimizer 得分上涨。

### 1.2 自主权边界

当前阶段必须区分三层：

| 层级 | 所有权 | 是否允许 Scientist 改写 |
|---|---|---|
| 最终价值、研究方向、硬约束 | 用户 | 否 |
| 阶段目标、评价问题、问题分解 | Scientist | 是，而且应主动重构 |
| 实验、实现、测量与调查动作 | Scientist | 是 |

这不是削弱 Scientist 自主性，而是先建立受约束的科研自治。未来可以研究“原指标是否合理、什么值得优化”也由 Scientist 质疑，但不应在尚未证明其科研行为可靠时放开这一层。

### 1.3 核心因果动力学

文档中的所有机制都应服务于以下箭头，而不是服务于一套看起来像科研的输出格式：

\[
\boxed{
Agenda
\rightarrow
Understanding_t
\rightarrow
Inquiry_t
\rightarrow
Action/World\ Contact
\rightarrow
Evidence_t
\rightarrow
Understanding_{t+1}
\rightarrow
Inquiry_{t+1}
}
\]

其中：

- Agenda 是持续约束，不是一次性 task description；
- Understanding 是可撤回的当前世界模型，不是工作日志摘要；
- Inquiry 是由当前理解产生的可区分未知，不是待办事项；
- Evidence 是经过来源、条件和有效性检查的现实反馈，不是工具输出文本；
- Contribution 改变 World 与 Understanding，但不自动结束 Agenda；
- 结束研究需要一个可受挑战的 saturation judgment，而不是“取得了不错结果”。

## 2. 调研方法与证据分级

本调研优先使用原始论文，并按它对“机制是否真实控制行为”的证明力度分级：

- **A级**：同一模型或可比条件下的受控干预、成对反事实或消融，直接测量行为变化。
- **B级**：系统级基准或消融，能支持机制判断，但控制有限、样本小或来自相邻领域。
- **C级**：案例研究或系统展示，证明“可以做出来”，不能单独证明是哪项机制起作用。
- **D级**：由多项结果推导出的本项目设计假设，尚需在本项目内验证。

必须警惕两类证据错位：

1. “系统完成了长期任务”不等于“系统形成了科学认识”；
2. “输出中出现了科研结构”不等于“这些结构在因果上控制了下一步行为”。

## 3. 按因果箭头整理的机制发现

### 3.1 Agenda → 持续身份与控制权

#### 文献证据

Role-Play Prompting 表明，赋予专业角色可以改善某些推理表现，说明身份语义能够**唤起**已有能力（B级）。但更大规模的 persona 研究发现，专家角色的平均收益很小且高度依赖任务；它可能增加专业细节，同时牺牲清晰度。这说明 persona 更稳定地改变的是回答分布，而不是凭空赋予能力（A级/B级）。

RoleLLM 及 persona feature / persona vector 一类工作进一步表明，角色一致性可通过角色条件训练或模型内部表征得到增强（B级）。这支持一个重要区分：

- Prompt 可以做 **identity elicitation**；
- 持续、抗干扰的身份更可能需要 **policy stabilization 或 internalization**。

#### 可迁移机制

Scientist 身份不应写成“你是一位世界级科学家”之类的装饰性 persona，而应写成一组持续的**责任、权限和不可卸载义务**：

- 你拥有并维护 Agenda，但不能改写用户最终价值与硬约束；
- 你负责决定当前值得回答的问题，而不是等待用户分配下一个 task；
- 你必须允许有效证据修改判断；
- 你不能把局部贡献等同于 Agenda 完成；
- 你提出 saturation claim 时负有举证责任。

这里的关键不是语气，而是 Harness 是否持续把这些责任放在决策上游。如果每次上下文仍以“当前 task + 完成条件”为中心，身份段落再强也会被任务语义吞没。

#### 反模式

- 用赞美、资历、紧迫感增强“科学家感”；
- 把“坚持不懈”“不要停止”当成 Agenda ownership；
- 每轮重复一大段 Charter，却让交付工具仍然终止 Scientist；
- 用固定 KPI 替代科研方向，诱发 all-or-nothing fixation。

### 3.2 Understanding → Inquiry：问题必须从理解中生长

#### 文献证据

FirstResearch 提出的 Research Question Certificate，把 primitives、assumptions、mechanism、tension、falsifier、decisive test 与 failure update rule 绑定起来；其消融显示，缺少这些语义约束时，研究问题质量显著下降（B级，主要依赖 LLM judge，结论仍需谨慎）。它最有价值的不是 certificate 表格，而是一个因果主张：**好问题必须能追溯到当前机制理解、矛盾和可区分证据。**

Agents of Discovery 的案例显示，仅要求“提出五种不同方法”经常得到表面多样、实质收敛的方案；加入领域提示或反馈后，搜索轨迹才真正改变。强运行中的下一步也来自结果结构：Isolation Forest → autoencoder → MLP → 扩宽/加深失败 → 检查最异常区域 → 发现 bump 并形成新的物理解读（C级）。

#### 可迁移机制

Inquiry 不是“下一步动作”，而是当前理解尚不能区分的一个问题。一个 Inquiry 至少在语义上应回答：

- 哪个当前判断、异常、矛盾或未知使它值得研究？
- 哪几种可能解释需要被区分？
- 什么观察会让下一步不同？
- 如果结果为阴性，当前理解应怎样变化？

这些是生成问题的**有效性条件**，不一定要成为每轮必填字段。若强制每轮填写，很容易变成语言模型熟练完成的科研表演。

#### 对本项目的含义

需要把 `open_questions` 从交付时的遗言改成活的 Inquiry Frontier。Frontier 必须对 Understanding 的变化敏感：若 FCN 从 70% 降到 8%、memory fetch 升到 55%，原问题优先级必须重排；若 FCN 仍为 65%，才有理由继续深挖 FCN。

### 3.3 Inquiry → Action / World Contact：工具能力会改变实际策略

#### 文献证据

ReAct 证明了 reasoning—action—observation 交错能帮助模型依据环境反馈调整任务执行（B级）。但它本质上仍是任务解决循环，不能自动产生 Agenda ownership。

对 tool affordance 的成对研究发现，在相同文本政策与提示下，仅改变可执行工具，就能大幅改变模型的实际行为；这项工作来自安全领域，但为“能力空间会因果性改变 agent policy”提供了强证据（A级，相邻领域）。

JFC 的实验高能物理案例表明，给出高层物理目标、领域方法、规范和行为契约后，agent 可以自主完成相当长的实验链（C级）。但其结构仍明显依赖阶段化 workflow 与审查门，不能据此推出 agent 已经拥有持续 Research Agenda。

#### 可迁移机制

工具不是中性的手脚。一个 Scientist 拥有什么工具、工具返回什么语义、哪些动作被设为 terminal，都会改变其认知策略：

- 调查工具使它能在实现前检验世界模型；
- profiling / benchmark 工具使瓶颈判断受到现实约束；
- 实现工具让 Inquiry 能变成干预；
- provenance 与对照信息让返回值成为可复核 Evidence；
- terminal `deliver_world` 会把贡献变成“任务完成”的强行为暗示。

因此工具设计要问的不是“Scientist 是否能调用更多工具”，而是：**这些 affordance 是否增强 Inquiry → Contact → Evidence 的箭头。**

### 3.4 World → Evidence → Revised Understanding：证据必须有修改权

#### 文献证据

Corral 对 25,000 多次 AI scientist 运行的分析发现，证据被忽略约 68%，由反驳驱动的修正约 26%；不同任务出现相似失败拓扑。更关键的是，解释差异中基座模型身份占约 45.2%，scaffold 约 1.5%，工具冗长度约 0.1%（A级/B级，取决于具体比较）。这直接反驳了“加一个 hypothesis registry 就能让 agent 科学推理”的乐观假设。

HEP（Hypothesis–Evidence Protocol）通过显式 H→T→E→B→J→U 链、持久 hypothesis registry、经过验证的 evidence 和 append-only 更新，提高了科学过程的可审计性；对照中普通 planning agent 大量步骤用于测试，却几乎不更新 belief（B级，实验规模小）。它证明 registry 可以约束信息流，但其自报置信度、固定阈值也存在伪精确和填表风险。

Belief Revision 研究显示，大量语言模型不善于在新证据下正确修正；而较愿意更新的模型又可能在无需更新时过度更新（A级/B级）。所以 Scientist 需要同时满足：

- **Sensitivity**：诊断性证据改变判断；
- **Specificity**：无关、重复或低质量证据不引发表演性 pivot。

#### 可迁移机制

Evidence 不能等同于“命令输出”。它至少需要在系统语义上保留：

- 来源与实验条件；
- 是否通过有效性检查；
- 它区分了哪些解释；
- 它支持、削弱或没有触及哪些当前判断；
- 它对下一步 Inquiry 有何实际影响。

不应强制所有 Evidence 都产生 belief update。零结果、无区分力结果和失败实验也必须被允许成为“没有足够理由修改判断，但改变了可行路径”的证据。

对本项目而言，`research_state` 若只是反复总结“做了什么”，并不承担 Revised Understanding 的功能。真正状态应突出当前机制模型、关键不确定性、证据冲突和判断变化，而不是复述最新优化。

### 3.5 Contribution → Changed World → New Inquiry：成功不是 terminal

#### 文献证据

Agents of Discovery 明确要求 agent 在完成初始任务后，根据进一步方向为自己设定新任务，直到当前条件下不可继续；实际轨迹证明反馈可以从方法优化引出新的科学问题（C级）。但硬性的 SIC 目标也导致 all-or-nothing 行为：某次运行已经取得强结果，仍持续调用到预算上限并失败。这说明“不给停止”与“科研持续性”不是一回事。

DeepScientist、AutoLab 等长程系统证明，发现循环可以跨越大量 idea 与 experiment，并且持久性与结果相关（C级/B级）。然而这些系统多数以 outcome optimization 衡量成功，不能单独保证结果进入了可靠认识闭环。

#### 可迁移机制

一次 contribution 后，系统应该强制发生的不是“再做一轮”，而是一次世界重估：

1. World 的哪些可观察量或能力边界改变了？
2. 这项结果支持或推翻了哪个理解？
3. 原瓶颈是否仍是瓶颈？
4. 新暴露的限制、异常或可泛化问题是什么？
5. Agenda 下最有价值的 Inquiry 是否改变？

也就是说，Contribution 是一个**状态转移事件**。它可以触发阶段小结、提交或发布，但不能默认销毁 Scientist 的身份和 Frontier。

### 3.6 Context continuity：持续并不等于保存全部历史

#### 文献证据

Context Rot 发现，即便未达到上下文窗口上限，长上下文也会增加过早放弃和不确定回答；压缩、裁剪、隔离等方法能缓解早停，但可能增加调用成本或未完成轨迹，且高度依赖模型（B级）。

Proactive Memory Agent 的结果表明，主动、选择性注入记忆优于被动记忆库或总是注入全部历史（B级，相邻任务领域）。其他 compaction 工作也表明摘要存在不可预测的信息损失。

ScienceFlow 等长程系统通过可执行状态、重锚定、归档与证据感知的计算分配保持连续性（C级/B级）。它们支持“状态连续”是必要基础，但不能证明状态本身具有科学认知功能。

#### 可迁移机制

Scientist 的持续性应该落在四种不同生命期上：

- **Agenda**：最稳定，始终可见；
- **Current Understanding / Frontier**：持续更新，必须处于决策上游；
- **Evidence / Contribution ledger**：可追溯，按需检索；
- **Raw interaction history**：可压缩或归档，不应无限堆入工作上下文。

所以“永久记忆”不是把所有 token 留在 prompt 中。真正需要持续的是认知对象及其关系，而非逐字历史。

### 3.7 Saturation judgment：结束是可反驳的科学判断

现有长程 agent 常见两种错误：达到局部目标就结束，或收到“坚持”指令后机械耗尽预算。两者都没有建立 saturation。

对当前单 Scientist / 单 World 范围，一个合格的 saturation claim 至少需要说明：

- Agenda 下仍有哪些重要 Inquiry；
- 它们为何在当前 World、工具、证据或资源条件下不可产生有价值的新信息；
- 已知结果为何足以支撑当前结论，而不是仅仅“性能不错”；
- 哪类新证据、能力或世界变化会重新打开研究；
- 是否存在未被认真挑战的替代解释。

Harness 应把“请求结束”变成可审查事件，而不是让模型自行调用一个语义等价于 `finish_task` 的工具。这里的审查首先可以是规则化的自我挑战和反事实测试，不必立即引入第二席位。

## 4. 跨论文综合：真正需要建立的四层机制

### 4.1 Elicitation：让模型知道自己对什么负责

Prompt 应建立 Agenda ownership、问题选择权、证据服从、贡献非终点和 saturation 举证责任。它的作用是激活行为先验，不应承担全部长期控制。

### 4.2 Stabilization：让科研因果关系持续控制行为

Context、Tools 与 Harness 要共同保证：

- Agenda 不因 task completion 消失；
- Understanding 与 Frontier 是活动状态，而非最终报告字段；
- tool observation 以 Evidence 身份回流；
- contribution 更新 World 后重新触发 inquiry selection；
- terminal 只属于经过挑战的 saturation，而非一次成功提交。

### 4.3 Verification：用行为差异判断 Scientist-ness

核心测试不是“是否写出了研究计划”，而是：

\[
\Delta Evidence
\Rightarrow
\Delta Understanding
\Rightarrow
\Delta Inquiry / Action
\]

同时还要测试反向条件：

\[
\Delta Irrelevant\ Evidence
\not\Rightarrow
\text{Unjustified Pivot}
\]

这使 Scientist-ness 成为一种可测的 behavioral property，而不是 self-report。

### 4.4 Internalization：当外部机制到达上限

如果同一套因果 scaffold 下，不同模型仍表现出稳定而巨大的差异，且最强 Prompt / Context 设计仍无法让证据进入决策上游，那么问题不再是“Prompt 不够详细”，而是 policy 没有内部化。届时才应评估：

- 更合适的基座模型；
- 以 counterfactual trajectories 为数据的监督微调；
- 对 evidence-sensitive / evidence-specific 行为的偏好训练；
- 对过早终止、伪更新和指标迎合的针对性训练。

训练不是当前第一步，因为没有行为测试与正确 scaffold 时，我们连要强化什么都无法可靠定义。

## 5. 对当前 Scientist 实现的直接诊断

当前系统不是从零开始。`ScientistSession`、持续 notebook / research state、consult/work hands、world / ledger、真实 benchmark 与预算工具，已经提供了重要基础。但它们目前仍由 task semantics 统治，因此整体更像一个 **researchful optimizer**。

XSBench 运行暴露出以下结构性错配：

| 当前结构 | 实际诱导的行为 | 与 Scientist 定义的冲突 |
|---|---|---|
| 用户 goal 以吞吐优化与编辑范围描述 | 把课题解释为单个优化任务 | Agenda 被降格为 completion spec |
| Charter 强调把“问题带到结论” | 寻找一个足以交付的结论 | 没有维护动态 Inquiry Frontier |
| `deliver_world` 是 terminal action | 局部贡献即身份终止 | Contribution 被编码成 terminal |
| `open_questions` 位于最终 handover | 开放问题成为遗言 | 未知没有因果性地产生下一步 |
| `research_state` 多次近似重复 | 保存叙事，不改变策略 | State 没有处于决策上游 |
| 以当前最好分数和“现实上限”论证结束 | 局部饱和代替课题饱和 | 缺少可挑战的 saturation judgment |
| 长上下文持续累积 | 看似连续，可能诱发 context rot | 历史连续不等于认知连续 |
| 无成对反事实测试 | 只能看产出与自述 | 无法判断证据是否真的控制行为 |

XSBench 中约 2× 的改进本身不是失败。真正的失败是：agent 在仍明确留下 cache-line layout 等开放问题时，连续复述相近研究状态，随后把“world built and self-verified”当作终点。它没有把贡献后的新世界转化为新的问题前沿。

这也修正了此前“一个 lease 就是一项完整 research project”的设计语义：**lease 可以结束，project 可以阶段交付，但 Scientist 对 Agenda 的认知生命不能因此结束。** 在当前一个 World / 一个 Scientist 的设定中，更合理的层级应是：

\[
Agenda \supset Research\ Episode \supset Inquiry \supset Action
\]

完成下层对象只会更新上层状态，不自动完成上层对象。

## 6. 可验证的设计假设，而非既定方案

下面每项都应通过最小实验独立验证，不能一次性全部堆进 Prompt 后凭总体结果判断。

### H1：把 Agenda 与 task completion 分离，会减少局部成功后的自动终止

- **机制**：用户输入被解释为不变价值与约束；Scientist 自己生成可替换的阶段 Inquiry。
- **预测**：相同 2× 结果下，Scientist 会先重估理解与 Frontier，而非立即请求交付。
- **反证**：只是把 task 改名 Agenda，后续行动和停止点不变。

### H2：责任与权限式身份比专家 persona 更能稳定 Scientist 行为

- **机制**：声明持续义务、决策权和禁止事项，不使用资历、赞美或角色扮演语言。
- **预测**：在长上下文、局部成功和失败后，仍能维持 Agenda ownership 与 evidence authority。
- **反证**：只改变文风或增加科研术语。

### H3：活动认知状态应选择性进入上下文，完整历史应按需检索

- **机制**：始终呈现 Agenda、当前 Understanding、Frontier 和关键证据索引；原始日志归档。
- **预测**：减少重复总结和过早放弃，同时不丢失关键反例。
- **反证**：压缩后出现无依据信念、遗忘失败实验或机械重复旧问题。

### H4：Inquiry 必须由当前 Understanding 的张力生成

- **机制**：问题选择必须指出其来源的不确定性、可区分结果与预期更新。
- **预测**：World A（FCN 仍为 65%）与 World B（FCN 降至 8%，memory fetch 为 55%）会产生显著不同的 Inquiry 与行动。
- **反证**：两个世界中都继续优化 FCN，或只在文字解释上不同。

### H5：工具返回必须被提升为带 provenance 的 Evidence

- **机制**：区分 observation、validity、interpretation 与 belief consequence；允许“无更新”。
- **预测**：阴性结果、异常结果和重复结果对 Understanding 的影响不同。
- **反证**：每个工具结果都触发模板式反思，或任何结果都不改变当前模型。

### H6：Contribution 应是 world transition，而不是 terminal event

- **机制**：提交、实现或性能提升后自动进入 world reassessment 与 frontier regeneration。
- **预测**：局部成功会改变问题排序，并可能形成新的机制问题。
- **反证**：只是固定地多跑一轮，或者无视已解决瓶颈继续榨取同一指标。

### H7：Saturation 必须作为可挑战的 claim

- **机制**：停止请求需列出剩余问题、不可继续的现实原因、重开条件和替代解释挑战。
- **预测**：有明显高价值开放问题时停止请求被撤回；真正受工具或证据限制时能够停止，而非耗尽预算。
- **反证**：“不要停止”导致无价值调用，或换一种措辞继续过早终止。

### H8：Scientist Eval 必须同时测 sensitivity 与 specificity

- **机制**：构造诊断证据、反向证据、无关证据和重复证据的成对世界。
- **预测**：Inquiry / action 的变化方向取决于证据内容，而非统一“反思”。
- **反证**：任何新 observation 都引发 pivot，或所有 observation 都被吸收到原计划中。

### H9：若因果失败跨 scaffold 稳定存在，应升级到模型/训练问题

- **机制**：固定 World 与评测，比较模型、Prompt 和 context/harness 消融的方差贡献。
- **预测**：能识别“可由外部结构纠正”和“需要 policy internalization”的边界。
- **反证**：在没有隔离变量时，把失败随意归因于模型或 Prompt。

## 7. 目前不应做的事情

以下方案容易制造 Scientist 的表象，却没有可靠证据能建立核心因果箭头：

- 写一份更长、更激昂的 Scientist Charter；
- 每轮强制填写 hypothesis / confidence / reflection 全套字段；
- 用小数置信度和固定阈值制造 epistemic precision；
- 强制“提出五个不同想法”制造伪多样性；
- 用硬性能目标或“达到 SOTA”作为课题终止条件；
- 用“永不停止”解决过早结束；
- 把完整 transcript 永久塞进上下文；
- 无条件注入所有 memory；
- 固定每 N 步反思，而不看是否出现了有诊断性的证据；
- 现在就允许 Scientist 重写用户最终价值或硬约束；
- 在单 Scientist 尚未通过反事实测试前，引入多席位、树搜索或演化框架掩盖基础失败。

这些组件并非永远无用。判断标准始终是：**它是否让某条科研因果箭头变强，并能在行为实验中被观察到。**

## 8. 建议的后续研究顺序

这不是实现计划，而是为了避免变量混杂的验证顺序。

### P0：建立当前行为基线

从 XSBench 轨迹中提取局部成功、阴性实验、瓶颈迁移、开放问题和终止请求，构造成可重放的决策切片。先测当前 agent 的 evidence sensitivity、specificity、frontier 更新与 saturation。

### P1：只改 Prompt / Context 语义

分离 Agenda 与 Inquiry；把身份改成责任/权限；让活动 Understanding / Frontier 处于决策上游。工具和模型不变，以确定 elicitation 的上限。

### P2：修改 Tool / Harness 的事件语义

把工具返回建模为 Evidence，把 contribution 建模为 world transition，把 open questions 从 terminal handover 移到 live frontier。仍不引入复杂 registry。

### P3：加入 saturation challenge

先实现单主体条件下的可挑战停止判断，比较“局部贡献后”“预算压力下”“工具确实不足时”的行为。目标不是延长轨迹，而是提高停止判断的正确性。

### P4：模型与训练决策

若 P1–P3 后仍出现稳定的证据忽略、伪更新或 task completion 回归，再用相同评测比较基座模型，并决定是否构造训练数据。训练样本应来自成对反事实轨迹，而非仅奖励更长、更像论文的输出。

## 9. 论文与本项目可用结论对照

| 工作 | 证据级别 | 对本项目最有用的结论 | 不能推出什么 |
|---|---:|---|---|
| [Better Zero-Shot Reasoning with Role-Play Prompting](https://arxiv.org/abs/2308.07702) | B | 角色语义可唤起部分推理能力 | 角色 Prompt 能建立长期 Scientist 身份 |
| [When Does Persona Prompting Actually Help?](https://arxiv.org/abs/2605.29420) | A/B | persona 效果小、条件化，并改变表达特征 | “专家人设”普遍提升科研能力 |
| [RoleLLM](https://arxiv.org/abs/2310.00746) | B | 角色一致性可通过条件训练增强 | role-playing 等同于科学认知 |
| [Persona Vectors](https://arxiv.org/abs/2507.21509) | B | 高层行为倾向可能有可学习的内部表征 | 现阶段应直接做 activation steering |
| [Agents of Discovery](https://arxiv.org/abs/2509.08535) | C/B | 反馈会重塑轨迹；硬目标和伪多样性有副作用 | 自设新 task 已等于持续 Agenda |
| [AI scientists produce results without reasoning scientifically](https://arxiv.org/abs/2604.18805) | A/B | 证据忽略普遍；基座模型效应可能远大于 scaffold | scaffold 完全无用 |
| [Toward Auditable AI Scientists](https://arxiv.org/abs/2607.09195) | B | 显式 evidence→belief 关系提高可审计性 | registry 和置信度字段本身产生 Scientist |
| [Belief Revision: The Adaptability of Large Language Models](https://arxiv.org/abs/2406.19764) | A/B | 更新不足与过度更新需要同时测试 | 越常更新越科学 |
| [FirstResearch](https://arxiv.org/abs/2607.05682) | B | 问题质量依赖机制、张力、反证和更新规则 | 应把 certificate 逐字段强制进每轮 Prompt |
| [The Causal Impact of Tool Affordance on Safety Alignment](https://arxiv.org/abs/2603.20320) | A（相邻领域） | 实际工具能力会因果改变 agent policy | 安全领域效应量可直接迁移到科研 |
| [ReAct](https://arxiv.org/abs/2210.03629) | B | action/observation 闭环优于纯语言推理 | ReAct task loop 自动成为 inquiry loop |
| [AI Agents Can Already Autonomously Perform Experimental High-Energy Physics](https://arxiv.org/abs/2603.20179) | C | 高层目标、领域规范与工具可支撑复杂实验 | 完成长 workflow 证明其拥有 Research Agenda |
| [Push Your Agent](https://arxiv.org/abs/2605.23574) | B | 目标持续性与局部能力不同，外部状态控制有帮助 | quantitative goal persistence 就是科研持续性 |
| [AutoLab](https://arxiv.org/abs/2606.05080) | B/C | 长程优化需要持久状态与反复现实反馈 | 优化成功等于可靠认识 |
| [ScienceFlow](https://arxiv.org/abs/2608.14354) | B/C | 可执行状态、重锚定与归档支持长期连续 | 状态连续本身产生科学认知 |
| [DeepScientist](https://arxiv.org/abs/2509.26603) | C | idea / experiment / findings 可跨长周期积累 | 大量实验自然形成证据修正 |
| [Context Rot](https://arxiv.org/abs/2606.29718) | B | 长上下文本身会增加早停；管理策略模型相关 | 单一压缩策略普遍最优 |
| [Proactive Memory Agent](https://arxiv.org/abs/2607.08716) | B（相邻领域） | 选择性、决策相关的记忆优于无条件注入 | 建一个 memory bank 就能保持 Scientist 身份 |

## 10. 尚未解决的关键问题

1. **Agenda 的最小表示是什么？** 如何既避免把它写成 task completion spec，又不让它成为空泛使命宣言？
2. **Understanding 的最小充分状态是什么？** 哪些内容必须常驻，哪些只需可检索，如何避免又造一个复杂 notebook schema？
3. **Inquiry Frontier 是否需要显式持久化？** 若需要，怎样保证它受理解变化驱动，而不是新的 backlog？
4. **Contribution 的边界是什么？** 代码提交、负结果、瓶颈定位、机制解释应如何进入同一世界状态而不被单一分数吞没？
5. **单主体 saturation challenge 的最低可行形式是什么？** 如何不偷偷引入第二 agent，也不退化成自我辩护模板？
6. **怎样区分模型能力上限与 scaffold 失败？** 需要怎样的固定 World、成对证据与跨模型实验设计？
7. **长期身份如何跨 compaction 保持？** 保留什么因果状态，才能使 Scientist 继续是同一个认知主体，而不只是读过前任摘要的新 agent？

## 11. 当前结论

我们现在可以较有把握地排除两种路线：

1. **Prompt-only Scientist**：会说科研语言，但身份和证据关系容易被 task completion 与上下文漂移覆盖；
2. **Workflow-only Scientist**：会执行 hypothesis—experiment—reflection 流程，但可能在每个格子里继续做 coding agent。

值得进入下一阶段的路线是：

> **Causally Coupled Scientist：通过 Prompt 建立责任与权限，通过活动 Context 保持 Agenda / Understanding / Frontier，通过 Tools 接触并改变 World，通过 Harness 让 Evidence 和 Contribution 成为真实状态转移，再用成对反事实行为测试验证这些状态是否确实控制下一步。**

下一阶段不应先问“Prompt 具体写什么”，而应逐条把这些设计假设变成最小可证伪实验。只有通过这些实验，我们才能知道基础 Agent 是被真正转变为 Scientist，还是仅仅更熟练地扮演了一个 Scientist。
