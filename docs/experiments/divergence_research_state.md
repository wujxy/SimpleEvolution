# ResearchState 发散性测试 — XSBench topk 单 run

> 状态：**完成**（2026-08-22 凌晨，`runs/divergence-v1/topk/seed-1`）。
> 目的：验证 research-state / transform / reframe-skill 改进后，proposer 是否还会
> "把父代 node 的方向直接拿过来用"，还是会自己发现新方向（树的宽度）。

## 0. 结论一句话

**发散性成立**：本 run 没有一个节点纯抄父代方向；6 次研究中 5 次要么 reframe 出新机制、
要么跨分支搬运已证机制、要么走与父代不同的机制。但发散是**倾向不是保证**——出现了一次
兄弟分支间的重复发现（running-seed 被两条线各提一次）。同时暴露出两个工具/技能问题：
`transform_worldview` 一次调用 400 直接炸掉整轮（**已修**），`reframe_inherited_problem`
skill 没有阻止那次重复发现（待议）。

## 1. 配置

`examples/xsbench_opt/task-fractal-divergence.yaml`，`--no-arm-override`：

- 分形树形状：`proposal_slots=3`、`max_research_per_node=1`、`frontier_top_k=3`、
  `max_proposals_per_node=3`
- 研究者预算 `scientist_steps=80`（从 200 降，控成本）
- 上限 `--max-evals 12 --budget-usd 4.0`；实际 **8 terminal evals，$3.53**，
  在 8M cap 饱和区诚实弃权后 quiescent 停止
- 前置修复：per-run `BENCH_PIN`、`RATE_PLAUSIBLE` gate（见 ablation_xsbench_design.md）

## 2. 树（9 节点，lookups_per_sec）

```
root 1.46M
├─ b3377621 (1.72M) running-seed + 插值搜索          [d1]
│   └─ 2b8100ee (4.55M) macro-XS 系数分解            [d2]  ← reframe
│       └─ 44b07abe (7.81M) 直接常量时间索引表        [d3]  ← reframe, frontier
├─ 025e3d53 (1.70M) bin-index 加速提示               [d1]
│   └─ 8ad5885b (1.92M) running-seed 重构            [d2]  ← 重复发现
│       └─ d5e0bdff (6.99M) macro-XS 移植(跨分支)     [d3]  ← 知识搬运
│           └─ 0b164db4 (7.78M) GRID_BINS 调参       [d4]  ← 微调, frontier
└─ 63a439a9 (1.01M) AOS→SoA 数据布局                 [d1]  ← 失败, 淘汰
```

双雄并进：44b07abe（7.81M）与 0b164db4（7.78M）分别通过**直接索引表**和
**macro-XS 移植+调参**两条机制栈逼近 8M RATE_PLAUSIBLE cap（97.7% / 97.2%）。

## 3. 逐节点行为分类（发散性判据）

| 研究节点 | 行为 | 分类 |
| --- | --- | --- |
| root | 3 个 proposal 覆盖 3 个机制：RNG 移除、搜索算法、数据布局 | 多样提案 ✓ |
| b3377621 | perf 重剖析 → 发现新瓶颈 `calculate_macro_xs` → 发明 macro-XS 系数分解 | **reframe ✓** |
| 025e3d53 | 重新发明 running-seed（兄弟分支 b3377621 已实现） | 冗余 ✗（局部理性） |
| 8ad5885b | 从共享 git 历史发现 cousin 的 macro-XS → 移植到自己世界 | 跨分支搬运 ✓ |
| 2b8100ee | 直接索引表替换插值搜索（transform 崩溃后 cold 重启仍 reframe） | **reframe ✓** |
| d5e0bdff | GRID_BINS 16384→48000（微调，接近 cap 的收尾优化） | 微调（有效但低创新） |

全树出现的不同机制 ≥6：RNG 移除、插值搜索、bin 表、SoA、macro-XS 分解、直接索引表。
**没有任何一个节点是纯抄父代方向。**

## 4. research-state 机制观察

- **6 个研究各注册 1 个 research_state**，`derived_from` 全部为 `None`——没有研究显式
  标记从父代 state 派生，都是重新形成自己的工作模型。b3377621 的 reframe 正是新的
  working model（"新瓶颈是逐核素插值"）驱动的。
- **child seed pack 按设计工作**：子代 scientist 启动上下文包含 proposal 特定 seed
  （当前 child 事实 + 实验 outcome + 父代 proposal/hypothesis，并明确标注
  "not an instruction / not an established fact"），开场白即要求"form your own
  working model；保留、修订、拒绝 memo 都是合法判断"。
- **`transform_worldview` 被调用 1 次（G5 反演），失败 0 成功**：400 崩溃（见 §5），
  修复后下一次 run 才有机会验证。

## 5. 发现的工具问题

### 5a. transform_worldview 崩溃（已修）

2b8100ee 研究在 step 43/80 调用 `transform_worldview operator_id=G5`，想用反演算子挑战
"DRAM 瓶颈"框架。但 `CognitiveTransformer._SYSTEM` 要求 plain text，而
`OpenAIChatModel._create` 对所有调用强制 `response_format={"type":"json_object"}`——
DeepSeek 要求 json_object 时 prompt 必须含 "json"，缺了就 400。且该 400 是 SDK 的
`APIStatusError`（非 ValueError/OSError），穿透 `execute()` 的错误捕获，**炸掉整个
research 轮**：42 步深度调研（gprof、noaccess 天花板测试、多次 lab 实验）全部丢失，
cold context 重启重做。

**修复（已提交前）**：
- `proposer/model.py`：`ChatModel.complete(..., json_object=True)` 开关；
  `OpenAICompatChatModel._create` 在 `json_object=False` 时不设 `response_format`；
  Anthropic 分支接收并忽略。
- `proposer/cognitive_transformer.py`：`transform()` 传 `json_object=False`（challenge
  是散文，直接注入，不依赖 DeepSeek 最不稳的 json 模式）。
- `proposer/research_tools.py`：`_transform_worldview` 把模型调用异常降级为
  `{"ok": False, "error": "cognitive transformation failed: ..."}` 观测还给 scientist；
  校验类 ValueError 保持原样。
- 测试：`test_transform_worldview_requests_plain_text`、
  `test_transform_worldview_failure_degrades_gracefully`。全量 158 测试通过。

### 5b. reframe skill 力度不足（待议）

025e3d53 加载了 `reframe_inherited_problem` skill，仍重新发明 running-seed。根因是
**知识盲区 + 程序性**：skill 没有跨分支视野（不知道兄弟分支已实现 running-seed），
且只教"怎么想"不给"想什么"。但同 run 的 8ad5885b 用共享 git 历史成功搬运了 cousin 的
macro-XS——说明**跨分支知识共享已能通过现有设施发生**，skill 只需把"提交前扫全树历史
检查 novelty"从可选变强制。**未修**：用户判断兄弟分支互不继承是演化架构的 merge 问题，
需单独讨论（见 §7）。

## 6. 后续

- [ ] 用修复后的 transform 重跑一次 topk，验证 G5 反演能实际产出 challenge 并被采纳。
- [ ] 讨论分支间 merge 机制（当前靠 scientist 手动 git 搬运，重复发现是结构必然）。
- [ ] 正式 3-seed 消融（预算充足后），把发散性从单 run 结论推广到统计结论。
