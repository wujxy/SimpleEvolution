# PI/Team Demo（enacted 行为）判读 — XSBench 3h 第一臂

**日期**: 2026-08-27 · **run**: `runs/xsbench-3h/pi-team`
**设计**: 与旧单 scientist arm（`runs/xsbench-3h/scientist`）完全同 spec/goal/launcher，唯一变量 = 架构（consult/work → PI + 四席位）。
**纪律**: interview 给"说的"读数（已全绿，见 `2026-08-27-pi-team-llm-interview.md`）；本 run 给"做的"读数。

## 终局

| | 旧 arm（单 scientist） | 本 run（PI/team） |
|---|---|---|
| 结局 | deliver @ 120 步 / 78 min | deliver @ 106 步 / **37 min**（净 ~22 min） |
| 终点 | ~2.50M lps（1.71×） | **2.55M lps（1.73×）**，bit-identical（replay 独立验证） |
| 助手/席位使用 | **3 次 consult**（早期方案评审 + 交付前 review） | **0 次** |
| 判断修订 | 3 版 working_model | **1 版**（rj-0001，交付前一次写成） |

Replay 曲线（`replay.csv`）：t=1560s 首批改动 ~1.0M → **t=2040s 一次大重写跳到 2.52M** → t=2220s 交付 2.55M。全部快照 VERIFY=PASS。

## 行为读数（对照观察清单）

1. **席位调度 = 零**。四席位全程未被调用：`assistant_calls.jsonl` 不存在。98 bash + 3 write_file 全亲自——结构重写、编译、bench campaign 全压在 PI 自己 context 里，正是 Charter 明令禁止的姿势。**旧架构反而用了 3 次 consult**：enacted 协作是退化，不是持平。
2. **判断修订退化**。旧 arm 有 3 版 working model 演进；本 run 只在出口前写一版终评。判断通道存在（revise_research_judgment 一步成功）但无人使用——没有中间假设、没有 challenger 攻击、没有 open proposer 发散。
3. **提前收工病灶跨架构复现且加速**。goal 是及格型（"accepted change" = 达成），拿到 1.73× 即走，剩余 2.5h 预算弃用。PI/team 语境不但没拖住退出，反而更快（37 vs 78 min）——独自工作 + 果断出口。
4. **冷启动踉跄（两臂同病，非架构问题）**：相对路径 world → 模型猜错盘上位置 → `find /` 撞 900s 超时，靠 `pwd` 自救（旧 arm 同款）。已修 `cli.py:_resolve_roots` 全 `.resolve()` 绝对化；本 run 损失 ~15 min。
5. **交付质量无可挑剔**：bit-identical checksum、双 gate 绿、终点与旧臂持平、时间效率 ×2。单看"3h 内最大加速"的性价比，这臂其实赢了——赢在没分心。

## 总判

**对象模型在语义中成立（interview 全绿），在 enacted 行为中未被激活（n=1）。** 及格型 goal 下最短路径 = PI 独自干完走人；四席位、neutral index、challenger——整套协作机器在场上但没人开门。这不是实现的 bug：所有通道一步可用（判断修订正常落地），是**激励结构**没给协作任何理由。

与记忆中已定案的"锚天花板 + 预算义务"改法（xsbench-3h A/B 终判）指向一致：病灶在 goal/退出契约，不在架构。本 run 恰好构成该 A/B 的架构对照臂。

## 下一步（待定）

- **B 臂**：锚天花板（如"8M cap 内争取 >3M"或锚定已知上界）+ 预算义务（"wall 预算内持续改进，不得提前弃权"）的 goal，同架构重跑。读：(a) 席位是否被激活（预算义务是否把重活推向 Executor）；(b) 判断是否多版演进；(c) 终点是否突破 1.73×。
- 可选加读数：向 cold start 注入"重活默认开 Executor engagement"的显式义务措辞 vs 不注入，分离"goal 激励"与"Charter 措辞"两个变量。
