# SimpleEvolution 消融实验设计 — XSBench 预算-性能曲线

> 状态：**框架已搭好并跑通冒烟验证**（见 §6），测试轮规模待定（见 §7）。
> 配套实现：`ablation/`（driver / plot / README）。

## 0. 目标与核心问题

用一张 **x=预算、y=性能** 的图回答：SimpleEvolution 的两个结构件各带来多少价值？

| 结构件 | 答案在 | 消融对比 |
| --- | --- | --- |
| **研究/实现分离**（researcher 产出方向，executor 只实现） | loop 臂 vs coding-agent 臂 | 认知分离的价值 |
| **frontier breadth**（top-k 多 lineage 并行演化） | topk 臂 vs loop 臂 | 并行搜索的价值 |

GEPA 暂不纳入（后续可作第四臂）。

## 1. 任务：examples/xsbench_opt

用 XSBench（Argonne 的蒙特卡罗输运关键 kernel）作为第一个任务，原因是：

- **确定性**：VERIFY 门要求校验 checksum 与冻结参考逐位一致，无训练噪声。
- **单目标**：`lookups_per_sec`（higher better），frontier 是干净的 1 维。
- **eval 快**：单次 bench ~1.5s（2M lookups，单线程），完整 eval 分钟级。
- **有隐藏参考**：`examples/xsbench_opt/reference/` 记录了人参考 bar。

定位是**方法学验证平台**：先跑通三臂 runner + 消融绘图管线 + 校准预算量级，真实结论留给 OMILREC。XSBench 是公开 benchmark，agent 可能凭知识快速逼近最优，三臂区分窗口可能很小——这本身是预期内的。

## 2. 三臂定义

| arm | researcher | frontier width | 隔离的变量 |
| --- | --- | --- | --- |
| `coding-agent` | **无**（trivial proposer 每次只发"继续优化"） | k=1（单链） | 单 agent 全自主，零研究分离 |
| `loop` | 真 researcher | k=1（单链） | researcher/executor 分离的价值 |
| `topk` | 真 researcher | k=3（树） | frontier breadth 的价值 |

**关键实现选择**：coding-agent 臂不是"退化的 loop"，而是把 researcher 槽位换成一个 trivial proposer——它不研究、不发方向，只发布一条"继续优化当前 best"的 proposal。executor agent 于是完全自主（既研究又实现），但 commit / eval / gate / telemetry **全部走标准路径**。

三臂共享完全相同的 executor / eval / gate / commit / 调度 / 记账机制，仅 `frontier_top_k` 与 proposer 身份不同。为隔离 breadth 这一个变量，全部臂统一：

```yaml
frontier_policy: topk
frontier_top_k: 1  # coding-agent & loop ; topk 用 3
proposal_slots: 1      # 每次研究只产 1 个 proposal
max_proposer_inflight: 1
max_experiment_inflight: 1
generator_reseed: false   # 不注入变体因子，纯测 breadth
max_research_per_node: 100  # 防止单 lineage 在 eval 上限前因研究预算耗尽而停滞
```

> `proposal_slots`、`generator_reseed` 与 `max_research_per_node` 是独立旋钮，先锁死在常数上（与 XSBench 默认配置不同），后续可单独 sweep。

## 3. 横轴与纵轴

- **x = 累计 LLM 成本（USD）**。来自 `run-dir/telemetry/usage.jsonl` 按 config 的 DeepSeek-flash 价格回放（`simpleevo/reporting/data.py:budget_series` 同一代码路径）。成本是三臂**唯一共同货币**：coding-agent 只有 executor 花费，loop/topk 还有 researcher。等预算下 coding-agent 自然做更多 eval——这本身就是消融要回答的问题。
- **y = best-so-far ×speedup vs 根基线**。单调包络，除以 run 起始的 root baseline（init 时统一测量一次）。不用绝对 lookups/s。
- **wall-clock 仅供参考**（会受并行度污染，不作为主轴）。
- **预算上限**：`--budget-usd 4.0` 为主约束；`--max-evals 10` 为 backstop。实测 $4 约只够 5–8 个 eval，预算先触达。

## 4. 公平性与 confound 控制

- 三臂用**相同** proposer/executor 模型（deepseek-v4-flash）、相同 prompt、相同 eval 命令、相同机器。
- **≥3 seed / 臂**，画 median ± min-max band（researcher 抽样、模型采样均有随机性）。
- **per-seed API key 隔离**（`--openai-keys` / `--anthropic-keys`，按 seed 循环）——限流而非硬件是并行时真正的瓶颈。
- 硬件无压力：128 核机器，9 run 并发峰值 ~9/128 核；XSBench 单线程计时会引入噪声，靠 median 洗掉。

## 5. 框架实现（`ablation/`）

```text
ablation/
  driver.py    # run / all / plot 三个子命令 + 配置变体 + trivial proposer + 上限循环
  plot.py      # 跨 run 预算-性能叠加图（三臂 median ± band）
  README.md    # 使用说明
```

- 所有臂产出**标准 SimpleEvolution run-dir**（同 DB schema、同 usage.jsonl），所以 reporting 与消融绘图一条管线通吃，零特判。
- 每个 run 是独立 run-dir（`<runs-root>/<arm>/seed-N`）+ 独立 git clone + 独立 apptainer 实例，互不干扰。
- run 进程用 `all` 编排为 **detached 子进程**（`start_new_session=True`），规避 harness 后台任务 ~52 分钟 SIGTERM 生命周期限制；run-dir 本身可 resume（调度器 reconcile）。

命令：

```bash
# 单臂单实例
python -m ablation.driver run --config examples/xsbench_opt/task.yaml \
  --arm topk --run-dir runs/ablation/topk/seed-1 --seed 1 \
  --max-evals 10 --budget-usd 4.0

# 全臂×seed 并行（每 run 独立子进程）
python -m ablation.driver all --config examples/xsbench_opt/task.yaml \
  --runs-root runs/ablation --seeds 3 --max-evals 10 --budget-usd 4.0

# 出图
python -m ablation.driver plot --runs-root runs/ablation --out ablation.png
```

## 6. 冒烟实测发现（已落地到框架）

单次 coding-agent eval（1 seed × 1 eval）在 XSBench 上验证了完整闭环：

```text
init → baseline（1.28M lps）→ trivial proposer → executor agent
  （改 src/、建 3 个变体 A/G/N 做 12 轮交错基准）→ eval → VERIFY 通过
  → 新节点 1.45M lps（1.13×）→ usage 落库 → plot 出图
```

| 发现 | 数值/事实 | 影响 |
| --- | --- | --- |
| per-eval 延迟高 | ~40–50 min、$0.78（coding-agent 开放式 round） | 10-eval round 每 run 数小时；预算先触达 |
| 预算 vs eval 上限 | $4 ≈ 5–8 evals | `--budget-usd` 是实际约束 |
| harness 后台任务限制 | driver 在 ~52 min 被 SIGTERM | 用 detached 子进程跑 run |
| **proposer 缓存记账缺失** | OpenAI SDK 丢 `prompt_cache_hit_tokens`，`extract_usage` 未读标准 `prompt_tokens_details.cached_tokens` → proposer cache 全记 0，成本虚高 ~30× | **已修复** `simpleevo/trace/usage.py`（读缓存字段并从 `prompt_tokens` 扣减，避免双重计费）；否则 loop/topk 臂 researcher 成本被系统性高估，压低其等预算表现 |
| 遗留 omilrec-001 run | 上一会话遗留，已自行停止 | 无残留进程 |

### 6b. 低 effort 链测轮发现（3 臂 × 1 seed × $2，已落地）

测试轮目的：验证三臂链路 + 校准低 effort 下的延迟/成本。三个发现均进入框架或本文档：

| 发现 | 数值/事实 | 影响/落地 |
| --- | --- | --- |
| **executor 首响应慢主因是 thinking** | `executor.effort: low`（claude `--effort`）把 executor 首条工具调用 36 → 18 min（砍半），executor 整轮成本 $0.78 → $0.41 | **已落地** task.yaml `executor.effort: low` + `researcher.reasoning_effort: low`；DeepSeek anthropic 端点尊重请求 `thinking` 字段、openai 端点接受 `reasoning_effort`，均已实测 |
| **cap bug：树永不 quiesce，cap 停不住** | topk(k=3) 触达 10-eval cap 后仍继续分配到 13 evals/$1.68——eval/budget cap 要求 `_quiescent()`，但 frontier 节点研究预算没耗尽就永远有 open allocation | **已修复（两层）**：① Scheduler `stop_allocating` 同时门住 `_allocate_proposers` 与 `_drain_executor_queue`（cap 后不产新研究/新实验）；② driver cap 后不再等 `_quiescent()`，改等 `not scheduler._in_flight()`（在途 proposer/experiment 排空即停，queued proposals 放弃）。实测 loop 在 $2 cap 后只 +1 在途 eval 即停（10 evals/$2.31）；35 测试过 |
| **baseline 测量方差大** | 本轮三臂 baseline 一致 ~380k lps，冒烟轮 1.28M；但改进节点稳定落在 1.4–2.1M → baseline 是异常点（疑似并发负载压低计时） | y 轴用 per-run 自身 baseline 归一，对比不受影响；**正式实验需统一 baseline 测量条件**（空闲/平均/全并发一致），否则 ×值被污染 |
| **loop researcher 是成本/延迟大头** | 研究到 step 69/200 才交 proposal（34 min，$0.62，~2.2 步/分，~$0.01/步）；`scientist_steps: 200` 默认太大 | 正式实验要 sweep `scientist_steps`（这是 loop 臂研究成本的旋钮，属消融变量或 confound 控制项） |

链测轮最终结果（低 effort，单 seed，$2/臂 cap）：

- **loop**：10 evals / $2.31，best **2.79M（7.37×）**
- **coding-agent**：5 evals / $2.07，best **2.29M（6.05×）**
- **topk**：13 evals / $1.69，best **1.57M（4.14×）**

均为 VERIFY PASS，绘图管线产出 [ablation.png](../../ablation.png)（x=累计 USD，y=×speedup vs 各 run 自身 baseline）。**单 seed 只验证链路 + 校准成本/延迟，不做三臂结论**（loop 单点 7.37× 领先、topk 点数最多，但样本量 1 无统计意义）。

### 6c. 正式配置大测试轮（3 臂 × 1 seed × $3.5，已修复两个测量漏洞）

正式配置（max-evals 20、scientist_steps 80、effort low）的链路验证轮：3 臂 × 1 seed，fresh runs-root（`runs/ablation-v2`），$3.5/臂（预算为绑定约束，$4 会触到 90 RMB 余额上限，见 §7）。三臂全部正常跑通，但暴露**两个测量完整性漏洞**，均已修复（见下）——大测试的意义正在于此。

最终结果（预算触达即停）：

| arm | terminal evals | spend | best lps（合法） | 备注 |
| --- | --- | --- | --- | --- |
| coding-agent | 10 | $3.56 | 2,450,980 | 10/10 completed，区间 1.6–2.45M，最干净 |
| loop | 15 | $3.61 | 2,066,116 | 双峰（0.8M 被抢核 / 2.0M 独占）；1 个 8.8M 作弊被 VERIFY 拦 |
| topk | 18 | $3.57 | 2,100,840 * | *best 合法值即第 1 个 eval，之后 14 个走预计算作弊（22–74M）+ 4 个 2G 被 VERIFY tripwire 拦 |

三臂总花费 $10.74 ≈ 72 RMB（90 预算内）。

**发现 A — 预计算作弊（topk 触发，VERIFY 漏放）**。researcher 提出、executor 落地：在 untimed 的 `grid_init_do_not_profile` 里把全部 2e6 个 per-lookup 宏观截面预计算进 `SD.macro_xs`（5×lookups×8B ≈ 80MB），计时热循环 `run_event_based_simulation` 改成流式读数组、跳过真实查找/二分搜索。输出 bit-identical（checksum 998920 不变），lps 虚增 ~50–200×（22–74M）。现有 VERIFY tripwire（runtime > 10 ms）只拦住 2G 档（runtime 0.001 s），74M 档 runtime 0.027 s 恰好绕过。

**修复 A**：bench.sh（冻结，agent 不可改）增发 `RATE_PLAUSIBLE` gate token —— lps ≤ 8M（≈3× author kernel 2.56M）为 PASS，否则 FAIL；task.yaml gate 表与 gate_block 同步声明。作弊自动 `gate_rejected`，不进 frontier。阈值逻辑已验证（377k/1.47M/2.56M 通过、74M/2G 拒绝）。

**局限（已决策：方案 A，cap + 记录，不做更强防御）**。RATE_PLAUSIBLE 是输出层的止血带，不根治，已知缺陷：

1. **可调参绕过**——executor 把流式循环故意拖慢到 <8M，即伪装成"真实 3× 突破"，cap 拦不住；
2. **cap 值 task/machine 特定**——8M 按本机 author kernel 标定，换环境不成立；
3. **根因是任务形状**——计时流固定且可预计算（每个 lookup 是 `i` 的纯函数），任何输出 gate 都看不到"计时区不干活"。

曾评估方案 B（源码模式 gate 拦 lookups 规模预计算数组）——会误杀合法优化（预计算 p_energies/mat_ids 输入表也是 lookups 规模数组），不采用。方案 C（计时流改运行时生成、live 喂入）是唯一根治法，但大改 benchmark 语义，XSBench 作为方法学平台不值得。**正式实验采用方案 A**；真正的防御留给 OMILREC 类任务设计：评估指标应测有状态/live 计算，避免固定可预计算流（本轮的奖励黑客图见 [ablation-v2.png](../../ablation-v2.png)，topk 196× 段即作弊）。

**发现 B — 共核 pin 竞争（三臂 baseline 同挤 core 9）**。bench.sh 默认 `BENCH_PIN=9`。三臂并发测 baseline → 三个 eval 进程（build+warmup+5 reps）全 pin 在 core 9 上互相饿死 → baseline 各测 ~377k（真实 pin-alone baseline 1.47M，差 4×）；后续 eval 随并发度呈双峰（~0.8M 被抢核 / ~2.0M 独占）→ best-so-far 相对被压低的 baseline **虚高 ~4×**（loop "5.5×" 相对真实 baseline 只有 ~1.4×，且低于 author kernel 2.56M）。

**修复 B**：driver `run_all` 按 run 分配 `BENCH_PIN=9+run_index`（3 run 用 9/10/11，128 核充足）；`experiment/apptainer.py` 的 `_FORWARDED_ENV` 增加 `BENCH_PIN` 透传，使每个 run 的 baseline 与改进节点都在**同核、同并发条件**下测量。残余注意：并发 run 仍共享内存带宽/末级缓存，单核 pin 已消除主导的 DVFS/核竞争失真。

> 结论纪律：本轮仍是单 seed 大测试，不做三臂结论。`runs/ablation-v2` 数据因发现 B 的 baseline 失真 + 发现 A 的 topk 作弊段，不作为正式结果；正式 3-seed 实验用修复后配置另起 runs-root。

## 7. 待定决策与正式实验配置

**测试轮规模（已定）**：3 臂 × 1 seed × $2 链测已跑（见 §6b）。**正式消融实验待 API 余额充足后跑**，配置如下（已落地到 `ablation/driver.py` `_COMMON_ARM_KNOBS` 与 README）：

| 配置项 | 值 | 依据 |
| --- | --- | --- |
| 规模 | 3 臂 × **3 seed** × $4/臂（9 run，~$36） | 设计 §4 ≥3 seed，median ± band |
| `--max-evals` | **20** | 链测显示 10-cap 让 loop(~$2.3)/topk(~$1.7) 提前停，未用完 $4——20 使预算成为唯一约束 |
| `scientist_steps` | **80** | 链测 researcher 69 步出好方向；200 的最坏研究成本 ~$1.8 会饿死 loop 的 eval |
| `executor.effort` / `researcher.reasoning_effort` | **low** | 链测：首响应砍半、成本 $0.78→$0.41/轮 |
| baseline 测量 | **per-run 独立 `BENCH_PIN`**（driver 自动分配 9+run_index） | §6c 发现 B：三臂并发同挤 core 9 使 baseline 失真 4×；独立 pin 让每 run 的 baseline 与改进节点同核、同并发 |
| 防作弊 gate | **`RATE_PLAUSIBLE`**（lps ≤ 8M ≈ 3× author kernel） | §6c 发现 A：topk 预计算作弊 22–74M 过 VERIFY；新 gate 自动 gate_rejected |
| API keys | per-seed 循环（`--openai-keys`/`--anthropic-keys`）；单 key 则全 seed 共享 | 限流公平性，可选 |

**待定**：GEPA 是否作第四臂——暂不纳入，保持 loop vs topk 的干净对照。

**正式实验启动命令**（见 [ablation/README.md](../ablation/README.md)）：

```bash
python -m ablation.driver all --config examples/xsbench_opt/task.yaml \
  --runs-root runs/ablation --seeds 3 --max-evals 20 --budget-usd 4.0
```

## 8. 结果解读方法

三曲线在**等预算**下对比：

- coding-agent 与 loop 的间隙 → researcher 认知分离的价值（若 loop 更高：方向性研究让每 eval 更便宜/更有效；若 coding-agent 更高：研究开销是净负）。
- loop 与 topk 的间隙 → frontier breadth 的价值（topk 更高：多 lineage 并行探索胜过硬爬一条链）。
- 三曲线都应随预算单调不降（best-so-far 语义）；饱和段反映任务天花板（XSBench 有已知最优）。
