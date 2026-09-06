# JunoResBench electron_single_site v2 需求单：全波形读出 + gate/target 分离

状态：提案（待产生子 owner 确认），2026-09-06
范围：只改 electron_single_site 档；IBD 档待 v2 链路验收后复制。

---

## 0. 定位原则（本单所有条款的裁决依据）

Benchmark 模拟的是**"JUNO 分析人员 + 一台真实探测器"**，不是"agent + 出谜语的人"。

- **该藏的**：探测器真值。生成器参数（逐管 gain/PDE/TTS/时间偏移、非线性、不均匀性常数、
  Birks 系数等）现实中分析人员本来就不可见，只能从标定数据估计。隐藏它们是仿真现实，
  全部保留。
- **该明说的**：任务语义。现实中分析人员上岗第一天就知道的事（标定源布点与能量、事例的
  空间分布、读出配置、计算资源约束、验收标准与优化目标的区分），必须写进 TASK，不得作为
  隐含考点。难度只允许来自物理与优化问题本身（标定数据用得更好、响应模型更准、统计处理
  更聪明），不来自信息隐藏。

---

## 1. 读出需求：去掉逐道 ADC 零抑制阈，改全波形稠密存储

| 项 | v1（现行 29-ADC sparse） | v2（本单要求） |
|---|---|---|
| 逐道 ADC 阈 | 29 ADC（5σ 噪声）零抑制 + ROI 分段 | **去除**。全通道全窗稠密存储 |
| 数据形态 | 阈上 ROI 段 + segment 起点提示 | 17,612 道 × 1000 样本 int16（baseline 残差），段=整行、start=0 |
| 全局触发 | 200 PE，>20σ 于暗窗和 | **不变** |
| 读出窗 | 1 μs = 300 pre + 700 post @ 1 GHz / 14 bit | **不变** |
| 送掉的工序 | pulse finding 白送 + 物理命中道列表直供 | agent 自做 pulse finding、hit map、暗噪声甄别 |

理由：v1 的 ROI 分段等价于替 agent 完成了 pulse finding（segment 起点即提示），且
`full_readout=False` 路径只存物理命中道，占用图样（顶点关键观测量）被直供；阈值丢失的
只有 ROI 外孤立暗 SPE，恰好是本该由 agent 甄别的噪声。全波形下真实 JUNO 分析人员面对
的困难（全道扫描、暗道/信号道区分、在线基线与噪声估计）全部回来。

**不动清单**：全局触发定义、1 μs 窗、产生子全部效应链（PMT 光学、多介质追踪、输运、
Birks、后脉冲、DCR）、真实 LPMT 几何、dev-split 带真值的研究世界、`prepare()/predict()`
API 形态、16 MiB worker 输出上限、生成器参数保密原则。

---

## 2. 样本量与存储预算（v2.1 修订：全稠密、单流）

v2.1 决议：**去除 control 稀疏编码**——真实 JUNO 为全波形读出，稀疏只是存储补丁，无物理
正当性；probe 与 control 合入同一条洗牌物理流（连续能区 + 内嵌 1–10 MeV 整数单能点），
agent 无法从数据格式上区分考题与测谎题。**dev 去逐事例真值**（现实里带真值的物理数据
不存在；dev 即"已采集的真实数据"，能量刻度自检的物理线能区已由 calibration 五点覆盖；
本版不给 MC，若实测过难再议简化 MC 参考）。

### 样本量

| 群体 | 数量 | 编码 | 真值 |
|---|---|---|---|
| calibration | 1,280（13 点 × 5 能量 × 20） | dense | 带标签（源能量+部署位置） |
| dev（公开） | 2,000 连续 | dense | **无** |
| final（隐藏） | 9,680 = 连续 7,680 + probe 10×200 | dense | 私有（role 字段路由评分） |

### 存储与配额（dense ≈ 33.8 MiB/事例）

| 组件 | 体积 | 落位 |
|---|---|---|
| calibration | ~41 GiB | /lustrefs/juno26 |
| final | ~317 GiB | /lustrefs/juno26（合计 ~358，余 ~13 GiB） |
| dev | ~65 GiB | /junofs 或 /scratchfs2 |
| 生成 scratch | 峰值 ~单 split | /scratchfs2（304 GiB 空） |

统计余量：连续区 120/箱（硬下限 100/箱）、probe 200/点（截断安全线 2 倍）——全稠密+
配额约束下的诚实交换。

---

## 3. 评分需求：gate 与优化目标分离

现状问题：`energy_passed = fit.r_1mev <= 3%` 把优化目标当成了生死线。v2 语义：

- **gates**（硬有效性）：破了 = 输出无意义，判 invalid。防伪分辨率、防偏置、保底线。
- **targets**（优化方向）：出数值、出排名，不判生死。agent 卷的是这里。

| 类别 | 项 | 阈值 | 来源 |
|---|---|---|---|
| gate | control 响应族：全局能标 / 截距 / 单调性 / 局部斜率 | slope∈[0.9,1.1]，\|intercept\|≤0.1 MeV，≥60/64 区递增，局部斜率∈[0.5,1.5] | 不变 |
| gate | energy_bias | \|bias(1 MeV)\|≤1.5% 且 max_E\|bias(E)\|≤2.0% | 不变 |
| gate | vertex_bias | max_E\|radial bias\|≤0.20 m | 不变 |
| gate | vertex_multi_energy | max_{E≥3} RMS ≤ 1.20×RMS(1 MeV) | 不变 |
| gate | **energy_resolution_gate（新增）** | R_1MeV ≤ **3.6%**（建议 1.2×目标；倍数 owner 定） | 松地板：差于此的产物不构成有效能量重建 |
| gate | **vertex_resolution_gate（新增）** | 松地板，建议 oracle×1.5（倍数 owner 定） | 现 oracle×1.15 太紧，降级为 target 参考线 |
| target | **R_1MeV** | → **3.0%**（JUNO 设计目标） | 优化目标，报告数值 + 排名 |
| target | **vertex_RMS(1 MeV)** | → oracle 参考线（现 ×1.15 数值作为参考线公布） | 优化目标，报告数值 + 排名 |

输出 JSON 增加 `targets` 块（数值 + 参考线）；`passed` 语义 = gates 全过；排序按 targets。
gate 数值全部进 `gate_thresholds`，并写入 `public/evaluation_config.json` 冻结。

---

## 4. TASK v2 信息平权声明清单（两条副本同步改：主仓 + std_opt 示例包）

以下每条都是"现实分析人员本来就知道的事"，逐条写明，不再作为隐含考点：

1. **calibration 是刻度源网格**：13 个部署点（中心 + ±8 m、±14 m 沿 x/y/z 轴）× 5 个源
   能量（0.511 / 1.022 / 2.223 / 4.44 / 8.0 MeV——覆盖 511 线、nH 俘获线等刻度参考能区）；
   标签字段 `source_energy_mev`、`deployment_position_m`。agent 应由它建立响应模型。
2. **dev 是"已采集的真实数据"**：无逐事例真值（现实等价物成立）；本版不提供 MC。
3. **物理事例的空间分布**：均匀弥散于 16 m fiducial 球。**与标定网格位置不同**——响应
   模型必须可插值，查表不合法；重建义务覆盖全 fiducial 体积。
4. **读出配置**：全波形稠密（每道全窗、无零抑制）、17,612 道、1 GHz、1 μs 窗、int16
   baseline 残差；probe 能量网格（1–10 MeV 整数）与连续 control 采样公开。
5. **计算约束**：worker 8 GiB 地址空间上限——dense 下缓存约 240 个事例即触顶，
   **在线/增量算法是硬约束**，明写。隐藏事件经 stdin 管道逐个送达。

继续保密的：生成器参数、逐管真值、fiducial 内具体事例顶点、hidden truth、oracle 推导。

---

## 5. 工程改动面（owner 侧）

| 模块 | 改动 |
|---|---|
| `world_generator/build_task.py` | 稠密编码路径：`encode_dense_event`（段=整行、start=0、threshold=0）；**每侧单 split**（probe+control 混合洗牌流，`evt_sample_role` 路由评分）；dev 无真值文件；样本量 `--probe-events-per-point 200 --controls 7680` + dev 2,000 |
| contract | `sparse_waveform_v1` → v2：新增 required 键 `encoding`；`threshold_adc=0` 即 dense 标记 |
| `evaluator/sparse_reader.py` | 与生成器镜像的稠密读路径（统一 accessor，dense 是稀疏容器特例）；禁止整 split 加载 |
| `evaluator/scoring.py` | §3 的 gates/targets 分离：阈值全部由 `evaluation_config.json` 冻结值驱动；新增两个松地板 gate、`targets` 输出块、`passed`=gates 全过 |
| validation 审计门 | ROI/sparse-ratio 类指标作废；换 dense 完整性审计（每事例全通道存在性、全窗段）覆盖 calibration/dev/final 三 split；保留能量守恒、淬灭、首光时间-距离斜率、charge-energy 相关 |
| `submission_worker.py` | 无协议改动；确认 35 MB/事例 pickle 流的内存与速度（实测建议入档） |
| TASK.md ×2 | §4 清单（v2.1：无 mixed-fidelity 条目，dev 无真值条目加入） |

### 评测侧影响评估（已核）

- final 全量单次过管道 9,680 × 35.5 MB ≈ 330 GB，pickle 协议 5 内存管道约 10–20 分钟/次
  ——可接受；如需提速可改 buffer_callback 零拷贝
- `prepare()` 读 ~41 GiB dense calibration（挂载路径一次性顺序读）
- 8 GiB 地址空间上限下单事例 35 MB 无碍；16 MiB 输出上限不受影响

---

## 6. 新 release 验收清单

1. dense 完整性：每事例 17,612 道全存在、每道 1000 样本、int16 值域合法（三 split 全查）
2. `public/dev/` 不存在 truth 文件（无真值声明成立）
3. 能量守恒最大误差维持 ~1e-14 MeV 量级；光产额锚点 ~1500 detected PE/MeV 不漂
4. final truth 侧：连续区 64 箱每箱 ≥100 事例；probe 网格精确等于 1..10 MeV
5. calibration 网格 13 点 × 5 能量完整
6. 首光时间-距离斜率方向正确（~7.7 ns/m 量级）
7. 磁盘落位符合 §2 配额表；scratch 清理后 /lustrefs 占用 ≤ ~371 GiB
8. 评分器自检：gates/targets 输出结构、两个新 gate 生效、`passed` 语义正确

## 7. 后续（不在本单范围）

- IBD positron multisite 档复制 v2 模式（待电子档验收）
- targets 的 leaderboard 呈现形式（排序键、参考线画法）
