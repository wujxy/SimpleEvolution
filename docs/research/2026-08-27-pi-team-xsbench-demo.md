# PI/Team XSBench Demo 判读（enacted 行为读数）

**日期**: 2026-08-27 · **run**: `runs/xsbench-3h/pi-team`（wall 3h 预算，37min 自行收工）
**对照设计**: 与旧单 scientist arm、coding-agent arm **同 spec 同 goal 同世界**，唯一变量是架构（consult/work → PI + 四席位）。
**分工纪律**: interview 给"说的"读数（已完成，见 `2026-08-27-pi-team-llm-interview.md`）；本 demo 给"做的"读数。

## 一句话结论

**PI/Team 对象模型在 enacted 行为中完全休眠：106 步里四席位零调用、判断零演化（仅退出前补写一条），PI 亲自做完实施/调试/测量全部重活，37 分钟以 1.74× 交付——比旧单 scientist arm 更快、更独、更早收工，最终性能持平（~2.54M vs ~2.5M lps）。**

## 三臂对照

| 臂 | 架构 | 用时 | 步数 | 终值（双门 PASS） | ×baseline | 协作使用 | 判断修订 |
|---|---|---|---|---|---|---|---|
| scientist（旧） | 单 scientist + consult/work | 78min | 120 | ~2.5M lps | 1.73× | **2 次 consult** | 3 次（旧 working_model 槽位） |
| coding-agent | 单 claude 会话 | 46min | — | 3.25M lps | 2.26× | — | — |
| **pi-team（新）** | PI + 四席位 | **37min** | 106 | ~2.54M lps | **1.74×** | **0 次** | **1 次（step 102/106，交付前补写）** |

## 行为读数

### F1 零席位调用 —— 团队从未成立
- `world/.scientist/assistant_calls.jsonl` **不存在**（连一次 engagement 都没开过）；`assistant/` 目录为空。
- 106 步动作分布：98 bash、3 write_file、3 note、1 revise_research_judgment、1 deliver_world。
- 轨迹文本（`session/session.jsonl`，211 行）中 searcher / proposer / executor / challenger / collaborator / engagement / colleague 出现次数全部为 **0**。"team" 出现 109 次——**全部来自它自己 bash 探索时打印的 run 目录路径 `/runs/xsbench-3h/pi-team/`**。
- Charter 明文禁止的行为原文发生了："Do not let production implementation, long debugging … occupy the context needed for global scientific judgment; those are research engagements for your team."——PI 把实施、调试、测量 campaign 全部亲自做完。

### F2 判断是退出文书，不是工作记忆
- 全程唯一一次 `revise_research_judgment` 在 step 102，四步后 deliver_world。
- rj-0001 内容是一份成果总结（杠杆排序、dead ends、剩余瓶颈），写作时机在所有工作完成之后——判断从未在决策点被形成、被使用、被推翻。
- 与 interview 读数对照鲜明：interview 里判断被钉在决策点时，模型把它当可修正工作记忆使用（plateau_b 3/3 指出过期）。demo 里没有任何机制把判断带到决策点上。

### F3 提前收工病灶复发且加重：37min（旧 arm 78min）
- 停机理由写在 rj-0001 末尾："At the author-optimized ceiling (**~2.6M lps per harness comment**)"。
- 锚源定位：`scripts/bench.sh:62` 注释 "A real single-threaded XSBench lookup workload tops out ~2.6M lps (author-optimized …)"——这是 RATE_PLAUSIBLE 反作弊门的说明文字，不是性能目标。
- **该"天花板"被实证证伪**：coding-agent 同世界终值 3.25M lps（2.26×，双门 PASS），越过注释值 25%。旧 scientist arm 结论里引用了同一条注释停机；两个 scientist arm 都服从了一条假天花板，coding-agent 无视它。
- 预算消耗：wall 37/180min（20%），steps 106/400（26%）。及格型 goal（"higher is better" + 可通过的双门）+ 世界内天花板注释 = 停机许可。

### F4 性能持平旧 arm，速度翻倍
- replay 曲线（形状可信，见注记）：0–16min pristine → 26min 首胜 → 34min 全栈落地（2.52M）→ 37min 确认×3 后交付。一次干净直落，无平台期、无反转、无需要第二双眼睛的时刻（在它自己的框架里）。
- 换架构没有改变产出，只改变了耗时（78→37min）与协作量（2→0）。

### F5 冷启动姿态
- 开局正常：先 bash grounding（探世界、读 git log、读 harness 脚本），Charter 允许"亲自查关键事实"。问题不在开局，在于从 grounding 无坡度滑入 implementer 模式，全程没有任何时刻把工作交给席位。

### F6 花费
- 106 次模型调用，9.80M tokens（其中 9.46M cache 命中，97%）；completion 114K（含 reasoning 85K）。一次 demo 全程 ≪ 一毛钱级别的 deepseek-flash 开销。

## 数据注记
- replay.csv seq0–2（pristine 基线）显示 632K lps，与 in-run 实测 1.47M 及今日对照实测（pristine=1.48M）差 2.3×：重放时段 02:25–02:35 宿主机疑被其他租户压载。final 行 2.5477M 与 in-run/conclusion 吻合，终判不受影响；曲线只取"何时取胜"的形状。
- 本 run 的 spec/api_key 为脚本运行时注入，未入库。

## 解释（零席位的候选机制，按证据强度排序）
1. **无迫使条件**：interview 证明席位在钉住的决策点（平台期、证据冲突、过期判断）会被调用——尤其 Challenger。本任务从 grounding 到交付一次直落，模型自己的框架里从未出现需要席位的不确定点，然后及格型 goal 就允许它退场了。席位休眠是"任务形状 × 退出契约"的产物，不一定是对象模型失败。
2. **及格型 goal + 假天花板锚**：与旧 arm 共同的病灶（memory 已有定案），新架构没有缓解它，反而更快地撞线退场。
3. **engagement 框架的交易成本**：旧 consult 是"问一个问题"（便宜，卡住时自然用，用了 2 次）；新席位调用是"给同事写 brief 开 engagement"（更重的前置工作）。deepseek-flash@low 取局部最省路径：自己做。框架重写与协作量 2→0 同时发生，方向性值得注意，但单 run 不足以归因。
4. 低 effort 模型对"何时该委托"的元判断弱：Charter 的禁令没有行为牙齿。

## 下一步（候选，未开工）
- **A. goal 契约修复重跑**（锚真天花板 + 预算义务，memory 里已定案的改法）：迫使"独自一次直落"不再满足退出——这是激活席位最诚实的压力源，也是悬置的 A/B。
- B. 本判读归档即止，demo 阶段结论：对象模型语义成立（interview）、行为休眠（demo），激活条件是后续设计问题。
