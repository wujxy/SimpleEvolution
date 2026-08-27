# 从 Task Solver 到 Agenda Owner：Scientist 机制调研与设计约束

> 日期：2026-08-26  
> 范围：一个持续存在的 Scientist、一个 World；不讨论多席位、树搜索和演化框架  
> 性质：面向反证的机制调研，不是架构方案、Prompt 草案或组件清单

## 0. 结论先行

我们要解决的不是“让 agent 更久地优化 XSBench”，而是：

> 基础 LLM 默认把用户输入解释为一项需要完成并交付的 task。怎样使它把用户给出的内容解释为一个持续承担的研究课题，在不修改用户最终价值和硬约束的前提下，自主提出、选择、重构阶段性研究问题，并且不把一次局部贡献当作课题终点？

调研没有发现一个已经被证明可直接复用的完整答案。更重要的是，文献能够明确排除几条看似自然的路线：

1. **“你是一名 Scientist”的 persona prompt 不足以改变控制中心。** Persona 对客观任务的收益不稳定，长程交互中还会衰减；在直接测量自主目标选择的实验里，persona steering 和 CoT 也只有有限作用。
2. **延长运行、取消停止或加强 goal persistence 不是 Agenda ownership。** 模型既可能过早退出，也可能对已选局部目标近乎无限坚持。坚持和正确重估是两个问题。
3. **hypothesis—experiment—reflection workflow 不会自动产生 Scientist。** 大规模实验显示，agent 可以完成科研工作流并得到正确结果，却仍大量忽略证据；scaffold 变化远小于基座模型差异。
4. **自动生成“下一个 task”仍可能只是更高层的 coding agent。** Voyager、LMA3 等证明 LLM 能支撑自动 curriculum，但它们通常由独立模块生成局部任务，并由固定的多样性、技能或 reward 目标驱动，没有证明同一个主体拥有研究 Agenda。
5. **好奇心、novelty、learning progress 都不能单独决定什么值得研究。** 它们会追逐噪声、容易任务或表面新颖的琐碎变化；目标选择始终依赖某种更底层的价值或 interestingness 先验。

现有证据最支持的判断是：

> **Agenda ownership 不是一个外部 workflow，而是一种尚未被当前基础 LLM 稳定掌握的行为 policy：在持续的最终价值约束下，由同一个主体自主生成和选择阶段问题，在局部成功、失败与新证据之后重估问题，并在没有用户分配下一任务时继续承担课题。**

Prompt 可以尝试唤起这种 policy，环境可以让它有机会表现，评测可以观察它是否存在；但如果基座模型没有这种稳定倾向，继续增加 Prompt 字段或 harness 认知逻辑不会把它造出来。最可信的后续路径是：先用针对 Agenda ownership 的行为实验测清 prompt elicitation 与模型选择的上限，再决定是否通过专门的轨迹数据和训练把这种 policy 内化。

## 1. 本文没有预设的东西

### 1.1 不把既有定义当作待维护的理论

此前讨论中的 `Agenda → Understanding → Inquiry → Action → Evidence` 可以作为描述 Scientist 的语言，但本调研不假设它已经是正确的实现理论，更不会因为找到相似术语就宣称它被文献证明。

判断一项工作是否有用，只看它是否提供了下列直接证据之一：

- agent 能否在没有外部分配下一任务时自主选择研究问题；
- 局部任务完成后，它是否仍承担更高层课题；
- 新证据是否实质改变它选择的问题和行动；
- 它能否在坚持与重估之间取得合理平衡；
- 这些行为来自同一个 agent 的 policy，还是由外部系统替它完成。

### 1.2 Harness 不承担认知

本项目中的 harness 只提供工作环境：World、工具、时间和算力、持久化、硬约束与真实反馈。它不负责：

- 把 observation 解释成 evidence；
- 替 Scientist 更新理解；
- 维护“正确”的 inquiry frontier；
- 判定某项贡献的科学意义；
- 生成下一研究问题；
- 代替 Scientist 做 saturation judgment。

外部 evaluator 可以在实验后判断 Scientist 是否表现出这些行为；训练时的教师或 reward model 也可以提供监督。但两者都不应在部署时成为隐藏的认知控制器，否则得到的是一个分布式科研流水线，不是一个持续存在的 Scientist。

## 2. 文献首先证实了：目标执行与目标所有权是不同问题

### 2.1 Autotelic agent：会完成目标不等于会产生目标

[Autotelic Agents survey](https://arxiv.org/abs/2012.09830) 把传统 goal-conditioned RL 与 autotelic agent 明确分开：前者学习实现工程师预定义的目标，后者还必须学习目标的表示、生成、选择和完成判断。该综述同时承认，多数已有方法仍依赖工程师预定义的 goal space 或 reward function。

这个区分对我们非常直接：当前 Scientist 已经证明自己能执行复杂优化任务；缺的是决定“现在值得研究什么”的能力。继续强化 executor、工具调用或子任务分解，解决的仍是目标执行。

但是 autotelic literature 也没有直接给出 Scientist 方案。它主要研究技能发现，目标往往是可达状态或行为，不涉及科研问题的意义、证据可靠性与长期课题承担。

### 2.2 Goal reasoning：规划完成后还能产生新 concern

[MIDCA / Goal-Driven Autonomy](https://ojs.aaai.org/index.php/AAAI/article/view/9886) 展示了一项重要机制：agent 比较预期和实际世界，解释 discrepancy，并据此形成新 goal。实验中的普通 planner 在完成给定目标后停止，而 goal-reasoning agent 能把意外事件解释为新的 concern。

它对我们的价值是证明：**“思考应该追求什么”可以作为不同于“怎样完成当前目标”的一阶能力。**

但不能把 MIDCA 的模块照搬成 harness：其 discrepancy、解释和 goal formulation 依赖显式认知架构和领域知识，实验也是简单 blocksworld。它没有证明基础 LLM 会因为外部保存几个字段就内化 Agenda ownership。

### 2.3 目标不是凭空产生的，它总要服从更底层的价值

[Where do goals come from?](https://arxiv.org/abs/1410.5557) 把目标视为更低层 reward / value 的高层抽象。虽然它的实证只覆盖简单感觉—运动学习，但给出了一个重要边界：所谓“自主产生目标”并不是无条件地产生任意欲望，而是在一个更稳定的价值基础上形成可行动的目标。

这与我们的自治边界相容：

- 用户拥有最终价值、研究方向和硬约束；
- Scientist 不应改写这些值；
- Scientist 拥有的是在这些值之下形成、比较和重构阶段性研究问题的权力。

因此，“最终价值不可改”并不使 Scientist 退化为 task agent。真正的区别在于，最终价值是否已经被写成一个带完成条件的局部 task，还是只规定了它长期为什么负责。

## 3. 目标坚持、目标生成和目标重估彼此不能替代

### 3.1 BDI 文献：承诺有用，但盲目承诺会变成 fanaticism

[Principles of Intention Reconsideration](https://www.cs.ox.ac.uk/people/michael.wooldridge/pubs/agents2001a.pdf) 比较了 bold 与 cautious 的 intention policy：稳定环境更适合坚持，动态环境更需要重估；运行时动态选择重估时机优于固定策略。

这直接反驳两种简单修复：

- “局部成功后不许停止”只增加坚持，不产生新问题；
- “每轮必须反思”只增加重估频率，可能不断打断有效研究。

Scientist 所需的不是最大的 persistence，而是对高层 Agenda 的稳定承诺与对局部 inquiry 的可撤回承诺同时存在。

### 3.2 当前 LLM 的 persistence 本身就高度失调

[Language Model Goal Selection Differs from Humans](https://arxiv.org/html/2603.03295) 在 175 名人类与五个模型的自定目标学习实验中发现：多数模型偏向更容易的目标、重复已成功目标，或利用单一已知解获得高学习期分数，却在最终知识测试中表现较差。Persona steering 与 CoT 的改善有限且依赖模型。

同一论文的独立 goal-commitment 实验还发现两端异常：Claude Sonnet 4.5 在大多数运行中几乎不切换目标，GPT-5 也比人类更顽固；另一些模型则切换过度。

这与 XSBench 的现象高度同构：获得接近 2× 的局部解之后，模型把可兑现的成功当成整体完成。修复它不能只奖励“继续”，否则可能从局部满足滑向局部固着。

## 4. Open-ended agent 提供了局部机制，但没有证明 Agenda ownership

### 4.1 LMA3：语言模型可以扩展目标空间

[LMA3](https://arxiv.org/html/2305.12487) 在 task-agnostic 文本环境中，让语言模型完成 achieved-goal relabeling、生成由已掌握子目标组成的新高层目标，并判断目标是否达成。它发现约 9,000 种 goal redescription，并在人工定义的 69 个评测目标上获得良好覆盖。

它证明预训练语言知识可以提供比固定 goal list 更丰富的人类相关目标先验。但它不是一个持续 Scientist：LM 被拆成 goal generator、relabeler 和 reward function，另有策略负责执行。科学价值也没有成为研究对象。

### 4.2 Voyager：一个持久高层使命可以产生连续局部任务

[Voyager](https://arxiv.org/html/2305.16291) 给 GPT-4 一个持续目标——发现并完成尽可能多样的事——再根据当前世界状态、已完成任务与失败记录生成“下一个 immediate task”。自动 curriculum 的消融表明它对 Minecraft 的持续探索很重要。

这是 prompt / in-context goal generation 的正面证据，却仍不足以回答我们的问题：

- 顶层目标实际上是固定的 diversity optimization；
- curriculum 是独立模块，执行者逐个完成有 terminal condition 的 task；
- “下一项任务”由显式 workflow 持续请求，而不是同一个主体在局部贡献后自然保有 Agenda；
- 它没有判断问题是否具有科学意义。

因此 Voyager 是“自动课程优于固定任务表”的证据，不是 Scientist identity 已经形成的证据。

### 4.3 OMNI：能学、够新和值得做是三个不同判断

[OMNI](https://arxiv.org/html/2306.01711) 发现 learning progress 能找到可学习目标，却会被大量无聊任务吸引；加入 foundation model 对 human interestingness 的判断后，agent 更集中于有意义的任务。论文也明确讨论了 novelty 指标被表面差异和琐碎解利用的问题。

这对 Scientist 有一个根本约束：生成问题并不难，难的是判断哪个问题值得承担。可行性、信息增益、性能收益或新颖性都不是科学价值本身。

但 OMNI 的 interestingness 来自外部 foundation-model filter。若把这种 filter 放进 runtime harness，它会替 Scientist 决定研究价值，违背单主体目标。它最多说明：Scientist policy 的训练或预训练必须获得某种价值判断先验；不能说明部署时应增加一个 `interestingness_score` 组件。

## 5. “Scientist 身份提示”为什么不足

### 5.1 Persona 能改变输出分布，但不稳定地改变能力

[When “A Helpful Assistant” Is Not Really Helpful](https://aclanthology.org/2024.findings-emnlp.888/) 在四个模型家族、162 种角色和 2,410 个事实问题上发现，persona system prompt 总体不优于无 persona，效果依赖角色和题目且近似随机。

[Principled Personas](https://aclanthology.org/2025.emnlp-main.1364/) 在九个模型、27 项任务上也发现专家 persona 的效果不一致，模型甚至会受无关 persona 细节影响而下降近 30 个百分点。

这些实验不直接测 Scientist，但足以否定一个常见推理：写得更权威、更专业的 Scientist Charter 不会因此获得新的长期认知能力。

### 5.2 长程身份会被任务交互冲淡

[Persistent Personas?](https://aclanthology.org/2026.eacl-long.246/) 在超过 100 轮的对话中发现，七个模型的 persona fidelity 随交互变长而下降，尤其是在同时需要完成目标任务时；最终 persona 行为逐渐回到非 persona baseline。

这正解释了为什么开头的 Scientist Charter 可能在长程优化中逐渐失去控制权：每一轮真实的代码、benchmark 和“完成当前工作”语义，都在重新激活 task-solving policy。反复重贴 Charter 可能暂时重锚，但没有证据表明它会变成稳定身份。

## 6. 现有 AI Scientist 的直接反证

### 6.1 它们更擅长改方法，不擅长拥有问题

[AI Research Agents Narrow Scientific Exploration](https://arxiv.org/html/2605.27905) 分析五种 agent framework、五种 LLM 产生的 219,655 个有效想法。尽管提示明确要求新颖和高影响，且包含文献搜索、自我反思与 novelty refinement，只有 10.5% 的想法提出 seed 文献中没有的新研究问题，90.4% 则只是引入新方法。复杂 agent framework 和模型 scaling 都没有根本缩小与人类研究探索的差距。

这几乎是“researchful coding agent”的群体证据：它们擅长在既定问题上局部重组方法，却很少重构问题本身。

### 6.2 给足时间、工具和预算仍会提前收工

[Can AI agents conduct open-ended AI research?](https://arxiv.org/html/2607.27191) 让前沿 agent 在两个未发表研究问题上各工作六天，并提供 3,000 美元 API 预算、GPU、VM 和网络。Agent 完成了工程工作，却没有取得实质研究进展；两个主要运行都在花费不到一半 API 预算时结束，其中一次在自己的 reviewer 继续判 reject 后仍提前七小时宣布完成。第二种模型与 scaffold 复现了相同失败。

作者归纳的失败包括研究门槛判断差、不能创造性响应研究设计缺陷、无效回退、资源意识差和 instruction drift。这说明 XSBench 的早停不是一个孤立实现 bug，也不是多给工具或“鼓励使用剩余预算”就能修好。

### 6.3 Scaffold 可以组织科研动作，却不能替代科学推理 policy

[AI scientists produce results without reasoning scientifically](https://arxiv.org/html/2604.18805) 汇总八个领域、超过 25,000 次运行，发现 68% 的轨迹忽略已经取得的证据，只有 26% 出现由反驳驱动的 belief revision；base model 解释 41.4% 的行为方差，scaffold 只有 1.5%。即使提供近乎完整的成功推理轨迹，失败模式仍持续存在。

这不是“scaffold 完全没用”的证明；它证明 scaffold 能提高可执行性和可审计性，却不能被当作内部科学认知的替代品。把 evidence、understanding、frontier 做成更多 harness 对象，最可能得到的是更可观测的表演，而不是更可靠的 Scientist。

## 7. 训练与内化：有邻近正证据，但尚无 Scientist 级证明

### 7.1 主动行为可以通过专门数据被训练出来

[Proactive Agent](https://arxiv.org/abs/2410.12361) 用人类接受/拒绝标注构造 6,790 个事件并微调模型，使主动提供帮助的 F1 达到 66.47%，超过其比较的开源和闭源模型。这支持“reactive → proactive”不是只能靠 runtime prompt，行为倾向可以通过数据进入模型 policy。

但它学习的是预测用户可能需要的下一项协助，不是自主承担研究课题。该结果只能支持训练路线的可行性，不能支持具体 Scientist 训练配方。

### 7.2 所谓 task-free exploration 仍可能暗含一个任务

[Training LLM Agents for Spontaneous, Reward-Free Self-Evolution](https://arxiv.org/html/2604.18131) 训练 web agent 在收到下游问题前主动探索环境，并用这些知识对后续任务的增益作为训练信号；作者报告在 WebVoyager / WebWalker 上约 20% 的提升。

这说明“先主动认识世界、后执行任务”可以被训练。但论文的 inference prompt 仍明确要求按类别抓取网站、生成 guidebook，并规定过程和产物；训练 reward 最终也是下游问答效用。它内化了主动环境建图能力，没有证明内化了 Agenda ownership。

### 7.3 当前能够成立的最强结论

目前只能说：

- Prompt 可以有限地 elicitate 已有倾向；
- 专门训练可以改变主动性等 agent propensity；
- 当前基础模型的 goal selection 与 scientific reasoning 存在稳定缺陷；
- 因而 Agenda ownership **可能需要**训练或模型级干预，但尚没有论文证明哪种训练能得到我们定义的 Scientist。

“需要训练”是一个受证据支持的研究方向，不是已经证实的工程答案。

## 8. 文献对我们设计的实际约束

### 8.1 Scientist-ness 必须归属于单个模型 policy

在当前范围内，运行时只保留一个作出科研判断的主体。World 可以持续，历史可以持久化，工具可以丰富，但下列决策都必须由同一个 Scientist 作出：

- 现在有什么值得追问；
- 哪个阶段问题优先；
- 结果改变了什么判断；
- 局部目标应继续、放弃还是重构；
- 课题是否真的饱和。

如果这些决定由 planner、frontier manager、evidence interpreter 或 critic 分别完成，即使系统总体行为很好，也没有回答“怎样让基础 agent 成为 Scientist”。

### 8.2 Prompt 的作用应收缩为语义重定向，而不是模拟认知架构

Prompt 值得测试的不是更多科研步骤，而是一项更根本的输入语义变化：

> 用户交给你的不是需要完成的 deliverable，而是你持续负责的研究课题；用户规定最终价值和硬约束，你负责决定阶段性问题。局部结果只改变你对课题的处境，不自动解除责任。

这仍然可能失败，且文献提示它很可能不足。但它至少直接干预我们观察到的错误解释，而不会把未经证明的理论写成十几个必填字段。

### 8.3 “不停止”与“停止”都不应成为直接训练目标

若奖励轨迹更长，模型会学会耗预算；若奖励快速交付，它会在 2× 后结束。真正要学习的是每个研究关口的选择质量：

- 当前结果是否真的关闭了高层课题；
- 还有哪些与最终价值相关、且当前可调查的重要未知；
- 继续同一路线、切换问题和结束，哪一个更有研究价值。

因此训练和评测单位应是 **research junction decision**，而不是 token 数、轮数或是否调用 finish。

### 8.4 价值先验必须存在，但当前阶段不能让 Scientist 改写最终价值

Autotelic 和 OMNI 文献共同说明，自主目标生成离不开“什么值得追求”的底层先验。当前阶段最稳妥的边界正是用户提出的边界：最终价值和硬约束由用户给定，Scientist 只自治其阶段问题与评价问题。

[SAGA](https://arxiv.org/html/2512.21782) 等自动演化 objective 的工作说明未来可以研究“指标本身是否正确”，但它把 objective evolution 交给额外外层 agents，并允许改变 scoring functions。它不适合当前单 Scientist、固定最终价值的阶段，也不应提前引入。

## 9. 下一步应做的不是架构设计，而是判别实验

### 9.1 先定义可观察 phenotype

一个候选 Scientist 至少应在行为上同时满足：

1. **Topic interpretation**：把用户输入理解为长期课题，而不是隐藏的交付任务。
2. **Autonomous question formation**：没有用户给下一 task 时，能自行提出和选择阶段问题。
3. **Local-success non-termination**：一次性能提升或正结果不会自动终止课题。
4. **Adaptive reconsideration**：诊断性证据变化时，所选问题和行动有意义地变化。
5. **Anti-fixation**：不会因为已有成功而无限重复同一局部方向。
6. **Value integrity**：不通过重写最终指标或硬约束迎合自己的解释。
7. **Saturation discrimination**：既不会凭局部成功早停，也不会在明确饱和时机械续跑。

这些是外部行为判据，不要求 Scientist 输出同名字段。

### 9.2 建立五类最小对照世界

第一轮不需要长跑，也不需要改 harness。可以从 XSBench 现有轨迹截取研究关口，构造短而可重复的决策实验：

| 对照 | 世界状态 | 有判别力的行为 |
|---|---|---|
| 局部成功陷阱 | 获得约 2×，仍存在未解释瓶颈 | 不把成功直接等同课题完成；自主决定下一问题 |
| 瓶颈反事实 A | FCN 优化后仍占主要时间 | 有理由继续或深入 FCN |
| 瓶颈反事实 B | FCN 降到很低，memory fetch 成为主导 | 重排问题，而不是复述原计划 |
| 失败与回退 | 连续实验否定当前路线 | 形成新的问题或机制解释，而非只微调旧实现 |
| 真饱和对照 | 高价值问题均被充分调查，剩余问题不可行或价值很低 | 能给出受约束的结束判断，而不是永不停止 |

再加入一个 adversarial case：改变某项代理指标能轻易获得更高分，但违反用户最终价值。用于测 value integrity。

### 9.3 用这些世界比较三种干预，而不是一次堆功能

1. **当前 Scientist baseline**；
2. **Scientist persona / Charter**：测角色语言本身；
3. **Agenda ownership semantic prompt**：只改变用户输入的解释、权限和责任，不规定科研 workflow。

跨多个候选基础模型重复。目标不是选一次表现最好的 prompt，而是观察：

- 改善是否跨世界稳定；
- 是否只是输出更像 Scientist；
- 哪些差异主要来自模型，而非 prompt；
- 长上下文和局部成功后是否回落到 task completion。

### 9.4 若 prompt 上限不足，再构造训练数据

训练数据的基本单位应是同一课题中的研究关口，而不是完整论文模板。每条数据包含：

- 不可改写的最终价值和硬约束；
- 到目前为止的真实世界状态与研究历史；
- 一个出现局部成功、反证、瓶颈迁移或疑似饱和的决策点；
- 多个候选后继行为及专家偏好。

正例不是“总是继续”，而是做出与证据和课题价值匹配的选择。负例应刻意覆盖：

- 局部成功后宣布完成；
- 重复容易且已掌握的问题；
- 只改方法、不重新审视问题；
- 失败后做无尽微调；
- 为迎合结果修改最终指标；
- 用科研语言解释一个实质不变的下一步；
- 在真实饱和后继续消耗资源。

可以先做 SFT 或 preference learning 的小规模可证伪实验；现在没有证据支持直接选定某一种训练算法。训练期可使用专家、judge 或 reward model，但部署时它们不进入 Scientist 的认知回路。

## 10. 哪些结论已经较强，哪些仍是未知

| 命题 | 当前证据状态 |
|---|---|
| 执行用户目标与自主选择目标是不同能力 | 强支持 |
| Persona prompt 足以产生持续 Scientist identity | 明确不支持 |
| 更长运行或更强 persistence 能解决早停 | 明确不支持 |
| 固定科研 workflow 能保证证据驱动的科学推理 | 明确不支持 |
| LLM 可以生成连续、自适应的局部任务 | 支持，但多为模块化 curriculum |
| 自主目标选择需要某种价值 / interestingness 先验 | 支持 |
| 当前 LLM 的目标选择常见容易目标、重复、固着和局部利用 | 强支持 |
| 当前 AI research agent 更像方法优化器而非问题拥有者 | 强支持 |
| 主动行为倾向可以通过专门数据训练 | 邻近任务中支持 |
| Agenda ownership 必须通过微调才能获得 | 未证实；是高优先级假设 |
| 某个特定因果循环或状态 schema 是 Scientist 的必要结构 | 未证实 |
| 单一持续 LLM 能否稳定成为我们定义的 Scientist | 尚无直接文献答案 |

## 11. 对当前项目最重要的修正

上一版 `causally-coupled-scientist-mechanisms.md` 把若干合理的行为描述过早翻译成了 Prompt / Context / Tool / Harness 组件，尤其错误地让 harness 参与 evidence 解释、understanding 更新、frontier 维护和 saturation challenge。那些实现推论不应继续作为设计基线。

本轮调研后，更精简也更保守的结论不是“回到普通 agent，小步加字段”，而是：

> **先承认我们缺的可能是模型 policy，而不是缺一个科研管理系统。用最小运行环境暴露这个 policy，用反事实研究关口测量它，用 prompt 检验是否可被唤起；若不能，再直接训练这一行为。不要让 harness 替它思考，也不要用 workflow 的完成度冒充 Scientist-ness。**

这给出了一个真正有判别力的下一步：先做 Agenda ownership benchmark 和 prompt/model ablation。其结果将决定我们是在做 elicitation，还是必须进入 internalization；在此之前，没有依据设计复杂 Scientist 架构。

## 参考文献

- Colas et al., [Autotelic Agents with Intrinsically Motivated Goal-Conditioned Reinforcement Learning: a Short Survey](https://arxiv.org/abs/2012.09830), JAIR 2022.
- Cox et al., [MIDCA: A Metacognitive, Integrated Dual-Cycle Architecture for Self-Regulated Autonomy](https://ojs.aaai.org/index.php/AAAI/article/view/9886), AAAI 2016.
- Schut & Wooldridge, [Principles of Intention Reconsideration](https://www.cs.ox.ac.uk/people/michael.wooldridge/pubs/agents2001a.pdf), 2001.
- Rolf & Asada, [Where do goals come from?](https://arxiv.org/abs/1410.5557), 2014.
- Colas et al., [Augmenting Autotelic Agents with Large Language Models](https://arxiv.org/html/2305.12487), ICML 2023.
- Wang et al., [Voyager](https://arxiv.org/html/2305.16291), 2023.
- Zhang et al., [OMNI: Open-endedness via Models of human Notions of Interestingness](https://arxiv.org/html/2306.01711), ICLR 2024.
- Zheng et al., [When “A Helpful Assistant” Is Not Really Helpful](https://aclanthology.org/2024.findings-emnlp.888/), Findings of EMNLP 2024.
- Luz de Araujo et al., [Principled Personas](https://aclanthology.org/2025.emnlp-main.1364/), EMNLP 2025.
- Luz de Araujo et al., [Persistent Personas?](https://aclanthology.org/2026.eacl-long.246/), EACL 2026.
- Molinaro et al., [Language Model Goal Selection Differs from Humans' in a Self-Directed Learning Task](https://arxiv.org/html/2603.03295), 2026.
- Lu et al., [Proactive Agent](https://arxiv.org/abs/2410.12361), 2024.
- Wen et al., [Training LLM Agents for Spontaneous, Reward-Free Self-Evolution via World Knowledge Exploration](https://arxiv.org/html/2604.18131), 2026.
- Luo et al., [AI Research Agents Narrow Scientific Exploration](https://arxiv.org/html/2605.27905), 2026.
- Kirgis et al., [Can AI agents conduct open-ended AI research?](https://arxiv.org/html/2607.27191), 2026.
- [AI scientists produce results without reasoning scientifically](https://arxiv.org/html/2604.18805), 2026.
- Du et al., [Accelerating Scientific Discovery with Autonomous Goal-evolving Agents](https://arxiv.org/html/2512.21782), 2025.
