# SimpleEvolution 实现交接文档

> 设计的 source of truth 是 [simpleevolution_design.md](simpleevolution_design.md)（§0 总览 + §7.2 GEPA frontier + §24 Parking Lot 是最新共识）。本文档只收录实现层决策：复用映射、MVP 拆分、坑清单。**两者冲突时以设计文档为准。**

## 0. 当前状态（2026-08-19）

- 设计已收敛：无 Reviewer；GEPA 式 per-axis winner frontier；identity-first；SQLite 单 writer；Gate = world validity。
- **M0–M4 骨架已落地，本地单元测试 33 项全绿，集成测试闭环通过。**
- 已接入：config/CLI、root node 创建、`reseed`、LocalSubmitter、Frontier 持久化、原子 ingest、reconcile、per-proposal L3 snapshot/fork、L2 查询工具、telemetry。
- CLI 已拆分为 `init`（建环境：git 就绪 + 镜像检查 + run-dir + root node）/ `run`（init 后正式跑）/ `resume`（从 run-dir/task.yaml 接着跑，reconcile 离线结果）；`pyproject.toml` 暴露 `simpleevo` console script。
- 仍存在的缺口见 §7。
- SimpleLoop 当前分支 `scientist-proposer-rsi`（~15.5k 行，`simpleloop/` ~10k + 独立 `proposer/` package ~5.5k）。

## 1. 复用映射（从 SimpleLoop 拿什么）

| SimpleEvolution 组件 | 来源 | 方式 |
| --- | --- | --- |
| Scientist runtime（proposer 角色） | `SimpleLoop/proposer/`（scientist.py / orchestrator.py / research_tools.py / scientist_session.py / model.py / runtime.py） | **抽成独立共享 package**，加 L3 snapshot/fork（见 §3） |
| 实验链路（executor 角色） | `simpleloop/stages/executor.py` + `stages/gate.py` + `stages/evaluator.py` + `world/git.py` + `world/apptainer.py` | 抽成独立 `experiment/` package，基本原样 |
| Job / worker / retry 语义 | `simpleloop/scheduling/`（envelope、jobs、hepjob、failure-path 状态机） | 参考模式，按演化语义重写，不照搬 |
| L1 trace 基础 | `simpleloop/persistence/journal.py`、`persistence/artifacts.py`、lane result 的 trace 字段 | 参考，包一层统一 envelope |

**明确不从 SimpleLoop 拿的**：`rsi/`（自我修改实验）、`app.py`/`loop.py`/`round.py`/`candidate.py`（round 编排层）、`legacy_config.py`、lane 的 self/viability 模式、judger 角色、winner selection、`insights.jsonl`。

## 2. 新项目架构

```text
simpleevo/
  scheduler/     # 事件循环、frontier 计算（per-axis winners + tie/hysteresis + f-weight）、
                 # executor queue（有界 FIFO + backpressure）、reconciliation
  db/            # L2：SQLite schema、ingest（唯一 writer）、查询投影
  trace/         # L1：统一 envelope（invocation_id / role / identity refs / events / output_refs）
  jobs/          # HTCondor 提交、worker envelope、Attempt 管理
  cli.py
proposer/        # 共享 package（与 SimpleLoop 共用同一份代码）+ L3 snapshot/fork 扩展
experiment/      # 共享 package：executor + gate + eval + worktree + Apptainer
```

规模预估：净新写 ~3.5-4k 行，系统总量 ~10-11k（小于 SimpleLoop 的 15.5k）。

## 3. proposer package 的改造点（唯一需要动的复用件）

连续性模型（trajectory + notebook.md + ledger、SUSPENSION/resume）**原样保留**——它就是 Scientist Thread 的物理实现。改造三处：

1. **Anchor 换绑**：`lane_id × round_id` → `(Thread, Node)`。Thread 走到 child Node 时 Node World 重绑定（新 SHA / metrics / gate 定义），Scientist 看到 world transition。
2. **L3 snapshot + fork**：Proposal 提交时刻拍认知快照；多个 Child 时从快照 fork（T7 → T7a/T7b）。底层 = 复制 session 文件 + 换绑 Node。
3. **Session 可搬运**：session 打包为 artifact，可被任意空闲 HTCondor worker 恢复。

**替换**：MemoryService 里以 round 为键的 history 投影 → L2 查询工具（search_experiments / inspect_node / …）。

估计：原样保留 ~80%，接口层改动几百行。

## 4. MVP Milestone 拆分

每个 milestone 都可独立验证，按序执行：

- **M0 — L2 地基**：SQLite schema（Node / Proposal / Experiment / Attempt / Thread / Frontier 状态 / Job）+ ingest 接口 + 单 writer 约束。验证：schema 支持 tree reconstruction 和 lineage 查询。
- **M1 — 单实验链路端到端**：手工构造一个 Proposal → Experiment Job（executor→gate→eval）→ Child Node 落库 + L1 trace 归档。在 OMILREC 真实任务上跑通一次。**这是整个系统最重要的 smoke test**，先证明实验链路在演化语境下成立。
- **M2 — Scheduler + Frontier**：事件循环、per-axis winner 计算（tie band + hysteresis）、f-weight proposer 分配、executor queue backpressure、reconciliation。验证：合成 Node 数据灌入 L2，frontier 计算和分配可 replay、可审计。
- **M3 — Proposer 接入**：Scientist 从 frontier Node 启动（fresh 或恢复 Thread）、L2 查询工具、Proposal 落库、L3 snapshot/fork。验证：Root 上 N 个 fresh scientist 并行提案。**注意 snapshot 拼接点**（§8）：L3 快照冻结在 Proposal 提交时刻、事后不改；实验结果由 Scheduler 在下次 Proposer Job 启动时从 L2 组装 world transition 记录，与快照一起作为 Job 输入——不要在 ingest 时往 L3 里塞结果。
- **M4 — 全链路 + 运维**：闭环跑通、quiescence 检测、frontier 健康度遥测（|Frontier|、单 lineage 占轴数）、可视化。

## 5. 坑清单（都踩过或讨论过）

1. **round 概念不许泄漏**：L2 schema、scheduler、prompt 里不得出现 round_id。时间轴是 Thread 生命周期，不是轮次。SimpleLoop 复用代码里 round 假设很多，移植时逐个审。
2. **SQLite 单 writer**：HTCondor job 绝不直连 SQLite（NFS/Lustre 上 WAL 不可靠，SimpleLoop 在 flock 上踩过）。Job 写 artifact，Scheduler 唯一 ingest。
3. **harness-owned metrics**：eval 输出 KEY=VALUE 由 harness 解析，LLM 角色永远不估数（SimpleLoop judger 的传统）。
4. **L1 需要 `claude -p --output-format stream-json --verbose`**，默认 JSON envelope 拿不到 tool calls。
5. **infra failure ≠ scientific failure**：gate fail 是完成的实验，不重试；网络断只产生新 Attempt。
6. **Frontier 计算必须纯机械**：tie band = benchmark 噪声底 + hysteresis，防测量抖动搅动分配。不引入任何主观评分轴；单轴时 Pareto 退化为 top-k，此时宁可回到朴素规则。
7. **Parking Lot 纪律**：新机制进主设计前必须能引用 L2/L1 证据。SimpleLoop 4.4k → 15.5k 的膨胀就是不执行这条纪律的样本。
8. **executor 空闲不是 bug**：不凑满 pool。

## 6. 建议给新窗口的开场指令

> 阅读 docs/simpleevolution_design.md（设计）和 docs/simpleevolution_implementation.md（本文件），从 M0 开始实现 SimpleEvolution。SimpleLoop 代码在 SimpleLoop/（分支 scientist-proposer-rsi），复用映射见本文件 §1。工作目录新建议仓或 SimpleEvolution/ 下新建 package，不动 SimpleLoop。

## 7. 已实现的 MVP 与已知缺口（2026-08-19）

### 7.1 已落地

| 能力 | 关键文件 | 验证 |
| --- | --- | --- |
| 任务 YAML 配置 + CLI `init/run/resume/status/inspect/reseed` | `simpleevo/config.py`, `simpleevo/cli.py` | `tests/test_cli.py` |
| `simpleevo` console script（`pip install -e .` → `simpleevo` 命令） | `pyproject.toml`, `README.md` | — |
| Root Node / Thread 自动播种 | `simpleevo/cli.py` | `tests/test_cli.py` |
| 本地子进程 submitter + manifest 形状对齐 | `simpleevo/jobs/local.py` | 集成测试 + CLI smoke |
| Frontier 持久化 + 原子 ingest | `simpleevo/db/store.py`, `simpleevo/scheduler/loop.py` | `tests/scheduler/test_frontier_persistence.py` |
| Reconcile（离线 result.json ingest） | `simpleevo/scheduler/reconcile.py`, `simpleevo/scheduler/loop.py` | `tests/scheduler/test_reconcile.py` |
| Per-proposal L3 snapshot + Child Thread fork | `proposer/cli.py`, `simpleevo/db/store.py` | `tests/scheduler/test_frontier_persistence.py` |
| L2 查询工具（inspect_node / inspect_experiment / search_experiments / compare_nodes / lineage） | `proposer/l2_memory.py`, `simpleevo/db/queries.py` | `tests/proposer/test_l2_memory.py` |
| Telemetry（frontier_size / lineage_axis_share / allocation_distribution） | `simpleevo/scheduler/telemetry.py` | CLI smoke 产生 JSONL |
| 实验链路 bug 修复（`ExecutionResult` 异常处理、`changed_paths` 回填） | `experiment/cli.py`, `experiment/runner.py` | `tests/experiment/test_*.py` |
| 真实任务示例 tiny_algo_opt（task.yaml + repo + apptainer.def + setup.sh） | `examples/tiny_algo_opt/` | `tests/test_example_config.py` |
| 配置相对路径解析（repo_path/runtime_image/prompt_dir 相对 task.yaml 目录） | `simpleevo/config.py` `load_config` | `tests/test_example_config.py` |

### 7.2 已知缺口（按优先级）

1. **真实 proposer 需要模型/API 配置**
   - 当前 `examples/smoke_task.yaml` 使用假 `runtime_image` 且未填 `researcher` 块，proposer worker 启动后会失败。
   - 真实任务需在 YAML 中提供 `researcher.base_url`、`api_key`、`model` 等字段。

2. **HTCondor 适配器缺失**
   - 当前只有 `LocalSubmitter`（本地子进程）。
   - 后续应新增 `simpleevo/jobs/htcondor.py`，复用同样的 manifest/result 路径约定，将 `Scheduler` 的 `submit_proposer`/`submit_experiment` 替换为 condor 提交。

3. **L1 Trace 完全接线未做完**
   - `simpleevo/trace/`（envelope + store）已创建，但 `experiment/agent.py` 尚未切到 `claude -p --output-format stream-json --verbose` 并写入 trace 文件。
   - 不影响闭环，但设计文档要求完整 L1 审计。

4. **Proposer 内部 round/lane 残留**
   - `proposer/` 仍有 SimpleLoop 的 `round_id`/`lane_id` 语义（如 `research_tools.py` 的 `inspect_episode` 提示）。
   - Thread/Node 主路径已可用，但需逐步清理 round 泄漏以符合设计纪律。

5. **Quiescence 窗口逻辑较浅**
   - 已实现“最近 N step 无新 proposal 才退出”，但未严格校验“Frontier 中每个 Node 都在最近窗口内被研究过”。
   - 当前对本地 smoke test 足够；大规模长时间运行时可加强。

6. **缺少真实任务端到端 smoke**
   - 已落地 `examples/tiny_algo_opt/`（从 `SimpleLoop/examples/tiny_algo_opt` 移植）：纯 Python 玩具仓库 + 符合 `EvolutionConfig` 的 `task.yaml` + `apptainer.def` + `setup.sh`，`tests/test_example_config.py` 锁定 metric 契约（CORRECTNESS/DRIFT 门 + ms_per_call 目标）。
   - 尚缺一次真实端到端运行：需要在可构建 Apptainer 镜像的主机上跑 `setup.sh`（本开发机因宿主 `/data` mount 问题构建失败，属环境问题非示例问题），并配好 `researcher` 模型后 `python -m simpleevo run --config examples/tiny_algo_opt/task.yaml`，验证 result.json、child SHA、metrics、gate、trace 归档。
