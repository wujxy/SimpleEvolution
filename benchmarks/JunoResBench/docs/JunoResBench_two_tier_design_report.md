# JunoResBench 双档位 Benchmark 设计报告

## 1. 目标

JunoResBench 的目标不是复刻真实 JUNO 的最终实验性能，而是用隐藏的、
物理完备的 JUNO-like 波形世界，区分“能读懂数据、建立响应模型并长期迭代”
的 agent 与只靠表面拟合的 agent。能量主目标固定为：

```text
R_1MeV = sqrt(a^2 + b^2 + c^2) <= 3.0 %
```

其中曲线为：

```text
sigma(E)/E = sqrt(a^2/E + b^2 + c^2/E^2)
```

该阈值独立于真实 JUNO 的 3% 设计指标；它是本 synthetic world 的 agent
验收线。

## 2. 两个档位

| 档位 | 物理拓扑 | agent 输出 | 验收 |
|---|---|---|---|
| `electron_single_site` | 1--10 MeV 单电子，多步带电径迹 | `E_rec,x_rec,y_rec,z_rec` | `R_1MeV <= 3.0%` 且 1 MeV 顶点 RMS 达标 |
| `ibd_positron_multisite` | 正电子动能径迹 + 两条 511 keV 湮灭 γ 多点链 | `E_rec` | `R_1MeV <= 3.0%` |

单电子档检验 waveform charge/time、位置依赖光收集校正、能量与顶点联合重建。
正电子档在同一世界上增加湮灭 γ 的 Compton/光电多点沉积和边界逃逸，检验
非线性、拓扑和位置-能量耦合的建模能力。

## 3. 共享隐藏世界与三类粒子

两个档位共享同一套隐藏探测器几何、光学、PMT 和电子学参数；不共享两个
任务的事件拓扑和评分输出。

- **电子**：使用 ESTAR/Bloch--Bethe 形状的 stopping-power 表逐步输运；
  每步记录位置、步长、步中点动能和局部 `dE/dx`。Birks quenching 按每一步
  `dE/(1+kB*dE/dx)` 计算，之后逐步 Poisson 产闪烁光，并按步长/β 产
  Cherenkov 光。
- **γ**：抽样相互作用距离；Compton 通过 Klein--Nishina 分布抽样，光电和
  低能截断将余能交给次级电子。所有次级电子回到电子输运链；γ 可在边界逃逸。
- **正电子**：初始动能先走带电粒子链，停止后严格产生两条背对背 511 keV γ；
  三 γ 分支关闭。两条 γ 分别进入 γ 输运链，因此最终事例是天然多点沉积。

所有逐步光子后续经历隐藏的 trace optics、PMT 探测、SPE/暗噪声/ADC 电子学，
最终仅输出稀疏波形。

## 4. 三方绝对代码隔离

```text
world_generator/       私有权威产生子
tasks/<task>/dataset/  波形与 metadata；不含 Python
tasks/<task>/evaluator/ 独立 reader、score、sandbox
```

产生子不 import evaluator；evaluator 不 import 产生子，也不 import 旧
`juno_res_bench`。二者唯一的关系是 `contract/*.json` 中冻结的文件格式。
产生子和 evaluator 分别实现 sparse waveform 的写入与读取；源码复用被禁止，
兼容性由跨进程黑盒测试验证。

公开 dataset 包含几何、标定波形及标签、开发集和开发 truth；私有 dataset 包含
final 波形与 final truth。公开树不得包含 seed、生成参数、逐步 truth、oracle
实现或任何可执行代码。

## 5. 评分与反投机规则

正电子档的连续控制样本只决定输出是否仍是合法的绝对能量估计器：64 个能区中
每区至少 100 事例，单调性、局部 slope、全局 slope `[0.9,1.1]` 与截距
`±0.1 MeV` 均必须通过。它不是第二个优化指标。该闸门阻止整体放大能标来
人为压低相对分辨率。

单电子档除同一能量有效性规则外，使用 1 MeV 探针的：

```text
sqrt(mean(||r_rec-r_true||^2))
```

作为顶点分辨率。私有产生子用理想 per-PMT charge pattern 的 Fisher
information/Cramer--Rao 计算其 oracle 限制，发布阈值为 oracle 的 1.15 倍，
向上取整至 0.1 cm。公开 evaluator 只从公开的
`evaluation_config.json` 读取冻结数值，不含 oracle 代码。

## 6. 在线评测与安全边界

submission 的 `prepare()` 只调用一次。随后 evaluator 将每个隐藏 event 单独
发送到 mount-isolated worker，立即收回不可撤销的预测。submission 可保留过去
事例的因果状态，但不能访问未来事件、全体 private data 或产生子。

评测 worker 使用 bubblewrap、8 GiB address-space 限制、1 小时 CPU/wall
上限和 16 MiB 文件输出上限。private data 目录不挂载到 worker。

## 7. HTCondor 生产流程

真实题库生成不在开发设备执行。集群入口为：

```text
world_generator/condor/jobs.tsv
world_generator/condor/generate.submit
world_generator/condor/run_generate.sh
```

在共享文件系统上设置 `JRB_REPO_ROOT`，确认每个 `jobs.tsv` 输出目录为空后，
执行 `condor_submit generate.submit`。设计时的默认 release 起始统计量为：

- 单电子：每个 probe 能点 1000，continuous controls 6400；
- 正电子：每个 probe 能点 10000，continuous controls 6400；
- 两者 calibration 均为每能量/部署点 20 事例。

本次电子 release 因共享盘容量约束，将单电子 probe 调整为每能点 200 事例；
该变化只降低统计精度，不改变任务定义、能量网格或 3.0% 验收目标。

生成结束后必须在同一集群运行私有 `validate_release.py`。它只读序列化后的
public/private 文件，生成数值门禁、16 张人工验收图和 ACCEPTED/REJECTED 标记；
不重新调用产生子，也不依赖尚不存在的专家重建算法。完整 validation 目录与私有
truth 一起保存，不发布到 agent 可见的公开包。

## 8. 发布验收清单

1. 私有 truth 能量守恒、低能局部 quenching、正电子两 γ 湮灭能量均通过；
2. public/private 递归 allowlist 无可执行文件或 truth 泄漏；
3. 固定 seed 可重生同一 truth 与 waveform hash；
4. 解析光电子统计预算支持 3% 目标；实际算法可达性明确标为 deferred；
5. waveform charge、hit pattern 和 timing 在最终序列化数据中保留物理相关；
6. 单电子 oracle 阈值已由实际 release 统计量生成并写入公开 config；
7. 在发布 bundle 中运行 evaluator 时不依赖 `world_generator/` 路径。

本报告冻结 benchmark 设计。实际生成日期、seed、oracle RMS、冻结顶点阈值、
baseline/reference 得分和 bootstrap 误差必须由 HTCondor release JSON 补充，
不能在生成前预填。

产生子的物理效应、受控近似和 1 MeV 分辨率预算另见
[产生子物理与分辨率预算](generator_physics_and_resolution_budget.md)。

## 9. 2026-09-02 单电子 release 实况

当前冻结 release 位于外部共享文件系统，不进入 Git。公开开发集共 9680 个事例：

- 1--10 MeV 十个 probe 点各 200 个，共 2000 个；
- 连续能量 control 7680 个；
- 顶点质量门槛为 `0.54 m`；
- 输出为 `E_rec,x_rec,y_rec,z_rec`，本 release 不评价 t0。

private truth 的发布前诊断得到：

| 检查 | 本次结果 | 结论 |
|---|---:|---|
| 事例数 / 输运步数 | 9680 / 1,060,415 | 每事例为多步沉积 |
| 最大能量闭合误差 | `8.88e-15 MeV` | 远低于 `1e-8 MeV` 门槛 |
| `<50 keV` 局域可见比例均值 | `0.88299` | 明显低能下压 |
| `0.5--2 MeV` 局域可见比例均值 | `0.98224` | 与低能区有清晰分离 |
| 1 MeV 平均 `E_vis/E_true` | `0.97518` | 与局域 Birks 预算一致 |

这些量只验证隐藏世界的粒子输运、quenching 和人口构成，不等价于重建性能。
完整 baseline 或最终验收仍需读取大体积波形；本次开发设备没有执行该步骤。

## 10. Research world 与发行边界

Coding agent 与 Scientist 使用同一个版本化模板：

```text
examples/junoresbench_electron_single_site_std_opt/
```

两者看到完全相同的 `/work` 和 public-only 数据。只有 `src/` 可写；任务说明、
evaluator、bench 脚本和数据均只读。宿主只把 `release/public` 映射到
`/data/jrb/electron_single_site_public:ro`，release 根、private truth 和
产生子均不进入 agent 容器。

初始 baseline 顺序读取稀疏 ROI，积分负脉冲电荷，通过公开 calibration 求能量
比例，并用部署点拟合 charge-centroid 的仿射顶点修正。它只用于提供可运行起点，
不代表达到 3.0%。同一源码版本的 verify/bench 通过源码哈希共享 `/scratch` 中的
预测，避免无意义地连续读取两遍约 100 GB 的开发波形。

本次 release 专属图保存在 `figures/electron_single_site_v2/`。其中总路径长度与
初始能量的相关系数为 `0.99914`，输运步数相关系数为 `0.88152`。这说明逐步
沉积和低能 quenching 已真实存在，但电子连续慢化仍接近确定性 CSDA 曲线；它是
当前基础档的显式简化，也提示未来若要继续提高任务机制复杂度，应优先加入可验证
的径迹/次级产生涨落，而不是人为增加接口或隐藏规则。

## 11. 当前未完成项

- 正电子多点档尚未在集群生成 release；
- 单电子 baseline、专家 reference 和 score bootstrap 属于长跑后的 deferred 证据，
  当前不作为 release 通过条件；
- 当前 Codex 所在 user namespace 无法再次嵌套 Apptainer，因此实际 mount smoke
  需从普通外层 shell 执行；静态挂载参数、权限契约和小型端到端 solver 已测试。

## 12. v2 实际波形人工验收

波形图不是重新调用产生子得到的，而是直接从冻结 `public/dev` 的 index 与
`segment_samples.npy` mmap 抽取 32 个确定性事例。总计读取 151,300,350 个采样，
占完整 49,817,589,448 个采样的 `0.304%`。中心与边缘 hit-pattern 选用同为 5 MeV
的 probe 事例，避免用能量差伪造位置差。

图位于 `figures/electron_single_site_v2/waveform_audit/`：

| 图 | 验收问题 | 本次观察 |
|---|---|---|
| `vertex_distribution` | 顶点是否填满球形体积 | 三个投影均为圆盘，密度向中心增加，符合体积均匀抽样投影 |
| `energy_radius_coverage` | 能量与位置是否覆盖完整 | 十个离散 probe 能点和连续 control 均覆盖至 16 m |
| `radial_light_yield` | 光收集是否有位置效应 | 中心较高、边缘有明显方向/半径涨落，必须做位置校正 |
| `hit_pattern_comparison` | hit 空间形状能否编码位置 | 5 MeV 中心事例近各向同性，16 m 事例明显向近端 PMT 聚集 |
| `charge_pattern_comparison` | 电荷纹理是否保留位置梯度 | 边缘事例出现强烈局域亮斑，中心事例无单侧集中 |
| `hit_multiplicity_vs_energy` | 占用数是否随能量增长 | 主趋势近似单调，中心/边缘几何造成可见散布 |
| `charge_vs_energy` | 波形积分是否保存能量信息 | 有符号基线扣除后近似线性；未校正位置造成离群和带宽 |
| `event_anatomy` | 单事件各观测是否互相一致 | 边缘亮斑、首光分布、积分-峰值和高电荷波形相互一致 |
| `first_hit_time` | 是否有 prompt 与晚光 | 约 300 ns 起出现 prompt 峰，并有散射、再发射和暗噪声晚尾 |
| `time_vs_distance` | TOF 距离依赖是否存在 | prompt 下沿随 PMT 距离增加而上升，晚光形成上方长尾 |
| `tof_corrected_residual` | TOF 校正后是否保留物理尾 | per-event 居中后 prompt core 清楚，正向长尾明显；50 ns core 内 RMS 为 `16.24 ns` |
| `timing_vs_radius` | trigger-relative 时间是否随位置变化 | 中位首光时间随半径和能量发生系统变化，可用于联合位置拟合 |
| `waveform_examples` | 单 PE 到多 PE 波形是否合理 | 均有约 10--20 ns 成形宽度；高电荷道可见脉冲叠加 |
| `waveform_overlays` | 模板形状是否稳定 | 对齐后主峰形状一致，噪声、晚光与 pile-up 产生合理展宽 |
| `pulse_integral_vs_peak` | 积分与峰高是否自洽 | 低占用形成线性核，高占用因脉冲重叠向大积分方向展开 |
| `roi_structure` | 稀疏化是否真的稀疏 | **未通过：ROI 几乎全部扩展并合并为完整窗口** |

### 12.1 必须修正的 ROI 问题

当前 `threshold_adc=6`；电子学白噪声为 0.35 mV，而 1 V/14-bit ADC 的一个
count 为 0.0610 mV，因此噪声 RMS 约 `5.73 ADC`，阈值仅 `1.05 sigma`。在每道
1000 个采样点上越阈几乎必然发生，16 个 pre-samples 与 48 个 post-samples 又把
相邻区间连成整窗。32 个抽样事例中：

- `95.10%` 的 ROI 从 sample 0 开始；
- `99.76%` 的 ROI 长度至少为完整窗口的 90%；
- 所谓 sparse waveform 因此实质接近所存通道的 dense waveform，解释了约
  100 GB 的 `dev/segment_samples.npy`。

这不是光学或电子学波形本身错误：采用 30 ADC（约 5 sigma）做 owner-side
脉冲选择后，位置亮斑、能量线性、TOF 下沿和脉冲成形都能看到。但它是当前题库
发行格式的实质缺陷，会增加 I/O 成本，并使未经二次阈值选择的“最早 ROI 时间”
完全由噪声决定。建议在产生子中把 sparse ROI 阈值冻结到约 30 ADC 后重新生成；
旧 release 可保留作诊断证据，但不应作为最终 benchmark release。

另一个可观测性限制是：波形时间以 trigger window 为参考，而 private truth 只保存
了 `t0`、没有保存 `t_trigger`。因此 owner 不能把两者做绝对时钟对齐；本图册使用
逐事例居中的 TOF residual，避免画出物理上无意义的 `sample_time-t0`。当前任务不
评价 t0，所以这不影响 agent 输出合同；若未来要验证绝对 timing，应在 private
truth 增加 `t_trigger`，但不能向 agent 公开。
