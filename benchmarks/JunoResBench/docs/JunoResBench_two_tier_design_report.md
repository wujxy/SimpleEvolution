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
执行 `condor_submit generate.submit`。默认 release 起始统计量为：

- 单电子：每个 probe 能点 1000，continuous controls 6400；
- 正电子：每个 probe 能点 10000，continuous controls 6400；
- 两者 calibration 均为每能量/部署点 20 事例。

生成结束后必须在同一集群运行私有 `validate_release.py`，以 public baseline、
私有 reviewed reference 和独立 evaluator 产生 release JSON。该 JSON 连同
私有 truth 保存，不发布到 agent 可见的公开包。

## 8. 发布验收清单

1. 私有 truth 能量守恒、低能局部 quenching、正电子两 γ 湮灭能量均通过；
2. public/private 递归 allowlist 无可执行文件或 truth 泄漏；
3. 固定 seed 可重生同一 truth 与 waveform hash；
4. public baseline 未达到目标，私有 reviewed reference 达到目标；
5. probe bootstrap 给出的 3% 边界稳定；
6. 单电子 oracle 阈值已由实际 release 统计量生成并写入公开 config；
7. 在发布 bundle 中运行 evaluator 时不依赖 `world_generator/` 路径。

本报告冻结 benchmark 设计。实际生成日期、seed、oracle RMS、冻结顶点阈值、
baseline/reference 得分和 bootstrap 误差必须由 HTCondor release JSON 补充，
不能在生成前预填。

产生子的物理效应、受控近似和 1 MeV 分辨率预算另见
[产生子物理与分辨率预算](generator_physics_and_resolution_budget.md)。
