# Scientist 工具分类与"身体入世"设计

状态：入世已实施（oneworld 路径，2026-08-25；旧 JSON 协议路径冻结为宿主生产路径，
待容器冒烟后一刀删）。分类（第一节）是 seat-v2 历史定稿，其不变量全部过墙保留。
关联：`席位制多proposer设计.md`、`科学家完整研究制设计.md`、交接文档
`docs/chat/2026.8.25.15.41.scientist入世交接.md`。

---

## 一、工具三分类（seat-v2 定稿）

seat-v2 之后、入世之前的工具面（15 → 清理影子后 11 个）：

### A. 通用 tool —— 世界接触层（眼睛、手、便签）

| tool | 作用 |
|---|---|
| read_file / grep_files / glob_files | 眼睛：读、搜、列（/work /repo /scratch） |
| run_research_command | 手：编译、运行、测量、git（有界 shell） |
| write_scratch_file | 便签：写 /scratch（heredoc 会毁代码，故有专用通道） |

两个刻意缺席：**没有 /work 写入/编辑 tool**——生产性世界修改只能走 work()（委托），
这是"production 归助手"纪律的结构保证；**导航义务**（读/搜/列先于 shell）写在描述里。

### B. claude tool —— 助手关系层（两个动词）

| tool | 作用 |
|---|---|
| consult | 信息通道：问/辩/审（read=none/node/lab）；**永不触碰世界**，返回 belief → 可随意高频调用 |
| work | 执行通道：做（continue/fresh 两世界，harness 逐次快照）；有副作用 → 需要 brief 与预算 |

恰好两个的根据：信息与行动的**风险不对称**是天然分界。审是 consult + read=lab 的模式，
不是独立能力，不设独立 tool。这两个 tool 是身份层"执行归助手、判断归己"的机制化身。

### C. research tool —— 架构专属（账本与租约语义）

| tool | 作用 | 必要性 |
|---|---|---|
| update_research_state | 租约的 Research State（六块），即时落账、逐次递增 | 核心仪器；没有它理解只活在轨迹里，会话一断即蒸发 |
| search_experiments / inspect_experiment | 实验账本：覆盖查询（防重复开采）+ 单实验全量溯源（唯一返回 proposal 文本的通道） | charter 的 closed-ground 纪律的数据源；裁决事实回流 |
| inspect_originating_research_state | 跨学派备忘录（刻意两步门：先 inspect_experiment 才能拉） | evolution 层的知识交换地基，防锚定 |
| use_research_skill | 方法加载（reframe + 扩展点） | claude_use 常驻后实际只服务 reframe，轻量保留 |

### 影子类（2026-08-25 已清除）

- list_findings / search_findings / inspect_finding：读一个**无写入方的存储**——findings 只在
  旧 proposal 的 research_target 路径创建，席位架构（deliver/abstain 出口）不写；probe-a-smoke-4
  的 DB 连 findings 表都没有。Generator 时代遗物。
- inspect_episode：dispatch 可达但不广告的旧寻址（r<round>c<candidate>），职责已被
  inspect_experiment 接管。
- 清理范围：specs、dispatch、MEMORY_TOOL_ACTIONS、执行 handler、fingerprint/telemetry 分支、
  两处注释。memory/service.py 内部的 Finding 存储保留（不在科学家面前，另一刀）。

### 跨类不变量（比分类更重要的骨架）

1. 眼睛先于手（导航义务）
2. 生产归 work，手只做区分性探针——没有 /work 写入 tool 是这条纪律的结构化身
3. consult 返回 belief，verified 由账本授予——认知防火墙，无人能自我加冕
4. 记录类 tool 一律 coverage-not-direction 语义——防从账本挖方向（ablation-v5 教训）
5. update_research_state 是唯一科学落账通道，落账即时——崩溃不蒸发调查

工具块顺序即使用优先级声明：B 类置顶（默认肢体）、update_research_state 次之（工作节奏）、
A 类眼睛随后、手与档案殿后。

---

## 二、身体入世（定案：一个世界的独立 scientist 包）

### 设计宪法（三次纠偏后闭合，逐条不可违背）

1. **scientist 是独立完备的 agent 包**（像 claude code）：脱离 simpleevo 也能单独跑
   ——给个目录当世界、给个目标，就能自己读码、做实验、叫助手，干完整轮研究。
   `python -m scientist.cli --spec spec.json --world DIR`。
2. **一个容器 = 一个世界**：scientist 与助手 claude 同容器、同文件系统、**能力完全相同**
   （bash + 读写）。没有内部沙箱、没有 git、没有快照——mount 就是边界，世界不需要
   自我管理机制。
3. **助手入容器 = 镜像里装 claude CLI + 凭据，仅此而已**。work() 就是在同一个世界上
   叫 claude 干活；consult 的权限边界用 claude 自己的 `--allowedTools`（无写工具）
   表达，不是沙箱；fresh 模式 = 世界里/scratch 下开个副本目录，用后删。
4. **依赖方向永远是 simpleevo → scientist**，scientist 包零 simpleevo import。
   与 simpleevo 沟通的窄腰 = spec.json（开门握手）+ conclusion.json（出口契约）
   + 世界里的 `.scientist/` 记录文件。
5. **harness 只在两个时刻在场**：开世界（mounts + worktree@node_sha + spec）、
   收世界（出口契约检查 → 快照 → 跑 eval）。**中间一律不侵入**——per-work 快照、
   HandTally 配额闸门、审计"助手每步改了什么"都是旧设计垃圾，已删。看过程只能
   事后读世界目录里留下的文件。
6. **功能不消失，所有权搬家**：预算/时间 nudge 是包自己的仪表（agent 拥有自己的
   循环，自己看得见钟）；出口契约（deliver 前须 state 落账、handover 词帽、abstain
   axes）在门口检查（`agent.validate_conclusion`），拒绝即观测回路继续研究。
   墙钟 kill 保留（那是关世界，不是管研究）。
7. **快照 = harness 给世界发身份证**（evolution 图的节点是 SHA；归因/复跑/防伪造），
   出口处对世界目录机械 commit，scientist 包看不见它；单机模式无快照。
8. **账本 = 世界文件**：research_state.jsonl / experiments.jsonl（harness 开世界时
   播种）/ assistant_calls.jsonl / usage.jsonl / conclusion.json 全在
   `<world>/.scientist/`。单机与 simpleevo 同形，无活通道——harness 事后读。
9. 模型传输纯 stdlib（urllib+SSE，`model_stdlib.py`）：容器镜像只有裸 python 3.9，
   无 SDK；宿主与容器同一代码路径。

### 纠偏史（防再犯）

- 造"宿主包装层"（inworld.py 中间人替 scientist 管模型管助手管落账）→ 被纠正：
  中间人拆掉，scientist 自带脑子自带助手。
- 想把宿主 hands 的逐次 git 快照搬进容器（gitdir 挂载）→ 被纠正：这是在 world 里
  再造 sandbox；快照是 harness 出口动作，本来就在原位。
- 问"agent 该不该自带脑子" → 假问题；独立包当然自带。

### 承重墙过墙表（分类一节的不变量全部过墙）

| 承重墙 | 过墙方式（已实现） |
|---|---|
| 出口契约（deliver/abstain + state-on-file） | 终态是原生 tool；`agent.validate_conclusion` 门口校验（handover 三键非空 + 词帽软400硬600 + handover_compliant 逃生阀；abstain 必须非空 axes_checked）；拒绝文本作为观测回给模型 |
| State 即时落账 | update_research_state 原生 tool，直写 `.scientist/research_state.jsonl`，逐次递增 |
| 预算（步数/墙钟） | agent 循环自己的仪表：80% 步数 nudge、80% 墙钟 nudge、10% 墙钟余量优雅 cut_off + notebook checkpoint；usage 流写 usage.jsonl |
| 边界（/repo 只读、editable 路径） | mount 级强制（入容器）；单机模式下 tool 参数双拼写（namespace /work /repo /scratch 经 PathBoundary 映射，real 路径经 `_normalize_path` 归一），bash 命令字符串用真实路径（boundaries 块如实渲染本世界的根） |
| 三层连续性（轨迹/notebook/账本） | session.jsonl（ScientistSession 原样进包）+ notebook.md + `.scientist/` 账本文件 |
| 跨类不变量（导航义务、生产归 work、belief/verified、coverage-not-direction） | 全部在 native 工具描述与提示块中过墙（NATIVE_TOOL_BLOCK 优先级声明顺序不变） |

### 包内布局（oneworld 路径，全部零 simpleevo import）

```
scientist/cli.py            agent 入口：--spec --world [--session] [--probe]
scientist/agent.py          原生循环 + nudge/suspend 文本 + 出口契约校验 + 上下文装配
scientist/world.py          LocalWorld：bash/read_file/write_file（PathBoundary 包含性检查）
scientist/assistant_tools.py InWorldAssistant：consult/work 拉世界内 claude CLI
                            （提示词/词帽300/fenced JSON tail/raw 落盘 .scientist/assistant/<call_id>/）
scientist/ledger.py         LocalLedger：研究态/实验档案（播种）/助手调用/usage，全 jsonl
scientist/model_stdlib.py   纯 stdlib urllib+SSE 流式 Chat Completions（含 tool_calls 分片组装）
scientist/model.py          原生 tool-call 通道（SDK 轨，宿主侧共用 _assemble_stream_reply）
scientist/native_tools.py   12 个工具 schema + 提示块 + native_actions/wire 助手
```

宿主侧旧 JSON 协议路径（ScientistAgent/orchestrator/宿主 hands）整体搬到
`simpleevo/jobs/proposer_worker.py`（base.py 模块表一行改），冻结不新增，
新路径过容器冒烟后一刀删。

### 时序（为什么旧路径还在）

容器镜像尚无 claude CLI——镜像补装前新路径无法进容器冒烟，生产 run 仍走旧 worker。
这是时序不是保留：冒烟通过 → 切 supervisor → 删旧路径（含 orchestrator/hands/runtime
的 A 类宿主执行代码）。

### 验证标准

- 单机 demo：真实 xsbench 世界 + 真模型 + 真 claude → 真 conclusion.json（三出口语义不变）；
- probe 行为 ≥ seat-v2 基线（对照 runs/probe-a-smoke-4：delivered/+36.8%/consult=1/work=5）；
- 全仓测试全绿；
- 容器冒烟（镜像装 claude 后）→ 切 supervisor → 删旧路径。
