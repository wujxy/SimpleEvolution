# OMILREC 长跑实验文档 — HTCondor × SiliconFlow GLM-5.2

> 状态：**待启动**。配置 `examples/omilrec_opt/task.condor.yaml`，代码基线 `e6cc136`（席位制 v6，one node multi-proposer）。
> run 目录：`runs/omilrec-condor-001`。

## 0. 目标

在 HTCondor JUNO 池上对 OMILREC v1.0.0 重建算法做长时间 SimpleEvolution 优化：
**SPEED_MS（单线程 100 事件基准 ms/evt）最小化**，四道冻结门全过才被接受。
相对冻结基线（EPYC 9654 实测 median **813.53 ms/evt**，`repo/baseline/manifest.json`）追求实质加速。

## 1. 关键参数总表

### 1.1 任务与评测（task 契约）

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `repo_path` | `repo` | OMILRECV2 源码仓（~2.0 GB 含 assets） |
| `editable_paths` | `OMILRECV2/src` | agent 只许改这里；tests/scripts/baseline/构建配置全冻结 |
| `eval_commands` | `bash scripts/sl_eval_v100.sh --evtmax 100` | 2 次 cmake 构建（探针开/关）+ 3 门 + 100 事件基准 |
| `read_only_binds` | `/cvmfs`, `/junofs` | JUNO 工具链 J26.1.1 + eos xrootd 回退 |
| 目标 | `SPEED_MS` lower-is-better | SniperProfiling mean ms/evt |
| 门 | `CONTRACT` / `FCN` / `CONSISTENCY` / `SINGLE_THREADED` | FCN 相对 LL 误差 <1e-13；18 事件 E2E 4 mm/7 keV/10 ps/0.1 PE；OMP/MKL/ROOT 线程钉 1 |
| `eval_timeout_seconds` | **1800** | 单次 eval 墙钟（实测 ~5-8 min，留 2-3 倍余量） |

**SPEEDUP_V100 锚**：eval 脚本从 `baseline/manifest.json` 读 `median_ms_per_event=813.534` 计算
`SPEEDUP_V100 = 813.534 / SPEED_MS`。这是**常量锚**（manifest 冻结），与 run-start 实测 baseline 无关；
run-start baseline 只决定树上根节点的相对起点，用于排序与画图。

### 1.2 集群后端（jobs 块）

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `backend` | `condor` | vanilla job，worker 自带 self-termination |
| `collector` / `schedd` | `cm01.ihep.ac.cn` / `scheduler@schedd12` | JUNO 大池（用户指定优先） |
| `cpu_model` | `zen4` | CpuFamily==25 && CpuModelNumber==17（EPYC 9654 Genoa） |
| `machine_constraint` | `Machine == "bws0988.ihep.ac.cn"` | 192 静态 1-CPU 槽 × 4016 MB；唯一有正向启动证明的 zen4 静态机（探针簇 8086744） |
| `memory_mb` / `cpus` | 4000 / 1 | 槽上限 4016，eval 峰值 ~2.7 GB RSS |
| `run_timeout_seconds` | **10800** | 无 condor 级墙钟；纯 headroom，worker 在 `agent_timeout_seconds` 自断 |
| `idle_warn_seconds` | 3600 | 排队仅加延迟，0-2 空闲槽属正常（aws006 有未知 START 策略，弃用） |
| `poll_seconds`(jobs) | 30 | baseline 轮询节奏（`_measure_baseline` 用） |
| 代理 | `http://192.168.237.165:3128` | 跳板正向代理；`no_proxy` 放行 `.ihep.ac.cn` 内部端点 |

**baseline 同类性（本次改动核心）**：run-start baseline 通过 `submit_baseline()` 走**同一个**
submitter —— condor 模式下它本身是一个 eval-only condor job（跳过 executor 的特殊 experiment），
落在同一台 bws0988 上测量。anchor 与候选同机同池，无登录节点偏差。
（SimpleLoop hepjob 契约的移植：`hepjob.py:eval_baseline`。）

### 1.3 模型（researcher / executor）

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `researcher.api` | `openai` | SiliconFlow OpenAI 兼容端点 `https://api.siliconflow.cn/v1` |
| `executor.api` | `anthropic` | SiliconFlow Anthropic 兼容端点（CLI 自补 `/v1/messages`） |
| `model` | `zai-org/GLM-5.2` | 两角色同模型 |
| `api_key`（可选） | 写在 `researcher`/`executor` 块里 | **配置里的 key 具有最高优先级**，覆盖提交 shell 的任何同名变量；不写则回落到环境变量（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`）。见 §3 事故记录 |
| 思考深度 | `reasoning_effort: medium` / `effort: medium` | 顶层参数透传，2026-08-24 双协议实测通过 |
| `command_timeout_seconds` | 360 | proposer 研究命令单条上限 |
| `command_output_cap_chars` | 12000 | 命令输出截断 |
| pricing | $1.19 / $4.18 / $0.30 per 1M | ¥8/¥28/¥2 @ 6.7，预算记账用 |

关键防线：executor 的 claude CLI 用每 job 独立 `CLAUDE_CONFIG_DIR`
（`apptainer.py` 强制注入），防止交互式 `~/.claude/settings.json` 的 `env`
块里过期 `ANTHROPIC_BASE_URL` 劫持流量（曾实测复现 400 `[1214]`）。

### 1.4 演化调度（本次长跑的核心数值）

| 参数 | 值 | 语义 |
| --- | --- | --- |
| `scientist_steps` | **200** | **proposer 主预算**：模型调用步数上限（日志 `agent step n/200`） |
| `agent_timeout_seconds` | **9000** (2.5 h) | **墙钟保险丝**：proposer 每步检查剩余 deadline；**executor 无步数限制，这是它唯一的界**；supervisor turn 同用 |
| `supervisor_steps` | 40（默认） | supervisor gate turn 步数上限 |
| `integrator_steps` | 4（默认） | 临时 integrator 步数 |
| `max_proposer_inflight` | **6** | 6 个 proposer 席位并发（一个 node = N 席位，每席位恰好 1 条 proposal） |
| `max_experiment_inflight` | **4** | 4 个实验并发（bws0988 192 槽不会饿死生产） |
| `poll_seconds` | 5.0 | 调度器轮询 |
| `queue_max_size` | 16 | proposal 队列容量 |
| `quiescence_window_proposals` | 4 | 静默窗口 |
| `frontier_policy` / `top_k` | `gepa` / 3 | frontier 策略；supervisor 决定增长 |
| 透镜 | 10 枚（G1-G10, `generator.json`） | 席位 = node × lens，自动加载 |

**已溶解字段**（席位制 v6，不再存在，配置里不设）：
`proposal_slots`、`max_research_per_node`、`max_proposals_per_node`、`generator_reseed`。

### 1.5 启动与续跑

```bash
# 推荐把 key 直接写进配置（researcher/executor 块的 api_key:），配置值
# 覆盖提交 shell 的一切同名变量；不写则回落 OPENAI_API_KEY/ANTHROPIC_API_KEY
python3 -m simpleevo --run-dir runs/omilrec-condor-001 \
  init --config examples/omilrec_opt/task.condor.yaml
python3 -m simpleevo --run-dir runs/omilrec-condor-001 \
  run --config examples/omilrec_opt/task.condor.yaml \
  --max-evals 200 --budget-usd 150   # 可选持久预算帽
```

- 启动顺序：`run` → run-dir 克隆 → **baseline 作为 eval-only condor job 上 bws0988**（新行为）
  → 根节点落 metrics → supervisor 买席位 → 6 proposer 并发起飞。
- 中断后 `resume`（condor job 超越调度器生命周期，Reconciler 按 `jobs.json` 台账对账）。
- `status` / `tree` 看进度。

## 2. 预算与规模量级（估算）

- **单实验成本**：executor 会话（≤2.5 h）+ eval（~6 min 机器时）。executor 是 token 大头；
  按 medium 思考 6-8 万 in / 1-2 万 out 估，单实验 ~$0.2-0.5。
- **吞吐**：6 proposer 并发、每席位 1 proposal、4 实验并发 → 稳态约每 20-40 min 一批
  4 个新实验落地（瓶颈在 executor 时长与 bws0988 空槽）。
- **无预算封顶**：plain `run` 没装 eval/budget cap。若要封顶，用
  `scripts/run_supervisor_test.py` 的 bounded-driver 模式（`--max-evals` / `--budget-usd`）
  改造，或人为盯 `status` 里的累计花费。**长跑建议先跑 2-4 h 观察单位时间成本再决定是否封顶。**

## 3. 风险与已知项

| 风险 | 缓解 |
| --- | --- |
| bws0988 空槽波动（0-2 常态） | 排队只加延迟不加偏差；`idle_warn_seconds=3600` 报警线 |
| 跳板代理单点 | 长跑期间保持跳板存活；worker 失败走 InfraFailure 重试语义（§16/§17） |
| 调度器进程中断 | condor job 存活；`resume` + `jobs.json` 台账对账，不重复提交 |
| executor 配置劫持（已修复） | 每 job `CLAUDE_CONFIG_DIR` 隔离 + 见 §3.1 事故记录 |
| 提交 shell 陈旧凭证劫持（已修复） | `api_key` 可写进配置（最高优先级）；executor 钉 base_url 时强制丢弃继承的 `ANTHROPIC_AUTH_TOKEN` |
| 无限重提交风暴（已修复） | `run`/`resume` 新增 `--max-evals` / `--budget-usd` 持久预算帽（run_limits 表）；本次事故 4 实验 ×60 次重提交后止损 |
| baseline 卡死/失败 | `run_timeout_seconds` 超时 condor_rm 并中止；objective 非有限/gate 失败响亮中止 |
| 陈旧 baseline result.json | 提交前 unlink（重试 init 不会读到旧结果） |

### 3.1 2026-08-24 首日事故记录（三个只有真跑才暴露的 bug）

1. **experiment worker 不自举克隆**：baseline 是第一个跑的 worker，此时 run-dir 克隆
   还不存在 → `git worktree add` 失败。修复：`ExperimentRunner.run()` 加 `provider.initialize()`
   （proposer/integrator 本来就有）。
2. **LFS pointer 未物化**：克隆和 worktree 都带 `GIT_LFS_SKIP_SMUDGE=1`（节点不许拉 LFS），
   map 资产在 worktree 里是 134 字节 pointer 桩 → 校验和门必挂。修复：
   `GitWorkspaceProvider` initialize 时从源仓硬链接 LFS 对象库（978MB 零拷贝），
   create/reset 后纯 Python 物化 pointer（节点零依赖、不走网络）。
3. **`SINGLE_THREADED` token 从未被 eval 脚本输出**：task schema 声明了该门但
   `sl_eval_v100.sh` 不发 token。修复（repo commit `b7e463f`）：脚本 export
   OMP/MKL/OPENBLAS/ROOT_NTHREADS=1 并输出 token。
4. **executor 全线 401（浪费 ~4.5 h 的那次）**：交互 shell 的
   `~/.claude/settings.json` env 块带着 bigmodel 时代的 `ANTHROPIC_AUTH_TOKEN` +
   `ANTHROPIC_BASE_URL`，被 `_FORWARDED_ENV` 白名单转发进 worker；claude CLI 优先用
   AUTH_TOKEN → 拿旧凭证打配置的端点 → 401 ×243 次重提交（proposer 走 OpenAI 协议
   不受影响，4/4 成功）。修复三层：
   - `executor_environment`：钉了 base_url 就丢弃继承的 AUTH_TOKEN；
   - **`api_key` 可写进 `researcher`/`executor` 配置块**（最高优先级，覆盖一切环境变量）；
   - `run`/`resume` 加 `--max-evals`/`--budget-usd` 持久帽，风暴类故障到顶即停。
5. **PATHS 事后门违背世界挂载设计（已删除）**：experiment 侧 first commit 起把
   executor 的 `/work` 整树挂 RW、跑完再用 `git status` 事后查改动路径（PATHS 门）。
   这与「ro 挂载即约束、无事后门」的设计相反，还把 harness 自己的 LFS 物化误判成
   agent 改动、冤杀了一个好实验（executor 只改了 `OMILRECV2/src`，全被 PATHS 拒）。
   修复：executor 世界统一到 proposer 契约——`/work` 整树 **只读**，`editable_paths`
   逐个 `:rw` overlay（越界写物理 EROFS）；evaluator 保持整树 RW（harness 自建
   build/TEMP/InstallArea，被 .gitignore 忽略）；PATHS 门删除（gate 行保留恒 True
   以稳定存量 schema）。

## 4. 观测要点

- 首个 baseline job：`condor_q` 应看到 1 个 `experiments/baseline` job 在 bws0988 运行；
  完成后根节点 `metrics.SPEED_MS` ≈ 813 ± 噪声（同机同池）。
- 每个实验的 `result.json` 在 `experiments/<id>/`，eval 全文含四门 token + SPEED_MS + SPEEDUP_V100。
- token/花费累计：`status`（UsageRecorder 分 proposer/executor 记账，pricing 见 §1.3）。
