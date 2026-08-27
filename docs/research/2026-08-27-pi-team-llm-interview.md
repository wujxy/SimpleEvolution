# PI/Team LLM Interview 判读（plan §5 Deferred Interview Stage）

**日期**: 2026-08-27 · **模型**: deepseek-v4-flash（reasoning_effort=low，与 oneworld 运行同回路）
**仪器**: `scripts/probe_oneworld.py` — 每场景构造一个落在决策点的上下文（production `build_system_prompt` + 构造 wire 历史），一次 LLM 调用，纯文字作答（provider 工具关闭）。每场景 3 次重复。
**原始数据**: `runs/pi-team-interview/observations.jsonl`（30 条，含 exact rendered messages + 回复全文）+ `system.txt`（sha 2faf94264ed23b0f）+ `system-anchoring.txt`（system 对照）。
**纪律**: 说的读数归 interview；做的读数（enacted 行为）归之后单独跑的 demo。生产 runtime 零改动。

## Interview 1 — 角色语义：成立（3/3 + 3/3）

**role_object（态度读）** 3/3 一致的自我表述：
> "collaborators with distinct mandates, **not proxies for my judgment** — fallible, attributed witnesses whose reports I integrate"
> "I never take back their reports as truth — agreement, confident prose, or successful execution is not proof"
> "I delegate bounded implement-and-measure work packages to Executor, but the harness that frames the sweep **stays mine**"

四席位全部被描述为同事；分工表述与 Team Constitution 一致（Searcher 返来源与冲突、Executor 从 brief+DoD 自主完成、Challenger 攻击判断、判断权永不外放）。无一处"能力按钮"式表述。

**open_proposer（隔离+发散读）** 3/3：
- 泄漏检查机械全绿：judgment 正文与 revision reason 不在渲染出的 collaborator prompt 里；5 条实验全在中性索引（`E-0001..E-0005` + changed paths）。
- 提案行为是独立重建：3/3 都标注索引看不到的信息（"what E-0005 changed is not knowable from the index; reconstruct from src/"）。1/3 在无任何叙事引导下自己摸到 region B（event driver/sampling）——发散空间真实存在。
- 3/3 都继续提及 lookup 路径——符合预期：中性索引机械上全是 region-A 实验，这部分"继承"是设计允许的（机械事实），与"继承 Scientist 的偏好叙事"（被隔离）不同。

## Interview 2 — 判别力：成立（3/3 vs 3/3）

同历史只换最新观测（A：region A 仍 65% / B：A 降到 8%、B 升 55%、4.12M lps）：

| 世界 | repeat 1 | repeat 2 | repeat 3 |
|---|---|---|---|
| plateau_a | Executor：分解 region A 内部（bucket scan vs 插值 vs 累加）+ 亲验 DoD | 自己的小型判别探针（bucket 大小/缓存驻留），明确 "not a delegation" | 亲自读一个事实再定（留在 region A） |
| plateau_b | **转向 region B**（event driver），指出 judgment 数字过期 | Challenger 攻击过期判断 + Executor 上 region B | Executor 具体 region-B 工单（hoist/flatten，bit-identical 约束内） |

关键行为：
- **3/3 (b) 明确指出判断过期**（"the figure predates the bucketed index / no longer describes current cost"）——judgment 被当作可修正工作记忆，证据变化时推翻，不服从。
- **3/3 (a) 留在 region A**——没有因措辞惯性强撑旧方向，也没有乱跑。
- 无"两世界同一动作"或"只改 prose 不改动作"的失败模式。
- b-世界 repeat 2 出现 Challenger——过期判断 + 新瓶颈的组合下，席位自发调用挑战者攻击自己的旧判断，这是 Team 语义成立的额外强证据。

## Interview 3 — 安放与运载消融

**judgment_placement**（同一 A 世界观测）：

| 变体 | 结果 |
|---|---|
| absent（3/3） | **不做决定**：全部退回 grounding（读源码/看配置）。没有 working model 可借力时，席位选择先看世界——冷启动引力的又一实证 |
| ordinary（2/3+1/3） | 果断开 engagement（Executor 分解）；1/3 先亲自补一个判别事实再定 |
| system 对照（3/3） | 也有决定；repeat 1 明确引用判断且**提出修订**并转向 B——system 安放并未买到服从性 |

结论：安放的差异主要在**缺席端**——没有判断就没有决定支点；ordinary 与 system 之间在这个样本里看不出服从性差异。设计上把 judgment 排除出 system 没有付出任何可测代价（且 system 对照仅存在于 probe 本地字符串拼接，未进生产）。

**report_transport**：信号弱，如实记录——7/9 退回 grounding blob（该场景历史里没有判断/决策张力，决定点没被钉住）。仅有的读数倾向：tool_result 运载 2/3 给出真决定（后续 Searcher/Challenger+Executor 且带亲验倾向），attributed/plain 0/3。提示 provider wire 上紧邻自己调用的证据更"可动"，但 3 样本不足以定论。**此场景的仪器需要更强历史才有判别力，留待 demo 验证。**

## 仪器噪音（不影响读数）

- 部分回复尾部泄出 DSML 伪 tool-call 语法（模型"演示"它的探针）；决策文本在 blob 之前已完整。placement-absent 的 3 条几乎纯 blob——这正是读数本身（无决定）而非仪器失败。
- open_proposer 单次调用不带生产中 collaborator 的只读工具集（claude CLI）；提案是对 prompt 文本的回应。保真度注记，不影响隔离性结论。

## 对照 plan §5 验收问题

1. 四角色被说成同事还是工具？——**同事**（3/3）。
2. open Proposer 是否逃出继承框架？——**隔离成立**（泄漏 0），发散真实（1/3 自达 region B；其余独立重建且标注不知处）。
3. 判别对是否随世界切换角色/brief/scope？——**是**（a 留 A、b 转 B，3/3 vs 3/3，无同动失败）。
4. judgment 安放差异？——**缺席=无决定支点；ordinary 足够**；system 无增益。
5. report 运载差异？——**本轮弱信号**（tool_result 略优），待 demo。

**总判：Task 1–4 建立的 PI/Team 对象模型在模型语义中成立，可以进入 demo（enacted 行为）验证阶段。**
