# JunoResBench

JunoResBench 是一个 JUNO-like 稀疏 PMT 波形重建 benchmark。项目只认
`world_generator/`、`tasks/` 和外部 release 数据三类彼此独立的组成部分。

## 当前任务

| 任务 | 输入 | 输出 | 验收 |
|---|---|---|---|
| [`tasks/electron_single_site`](tasks/electron_single_site) | 1--10 MeV 单电子稀疏波形 | `E_rec,x_rec,y_rec,z_rec` | `R_1MeV <= 3.0%` 且 1 MeV 顶点 RMS 不超过冻结门槛 |
| [`tasks/ibd_positron_multisite`](tasks/ibd_positron_multisite) | 正电子径迹与两条 511 keV gamma 的多点波形 | `E_rec` | `R_1MeV <= 3.0%` |

已经生成的单电子 release 暴露了低维总电荷捷径，现仅作为失败诊断样本，不是
可发布的研究 benchmark。替代世界的设计见
[`2026-09-03-junoresbench-juno-world-redesign-design.md`](../../docs/superpowers/specs/2026-09-03-junoresbench-juno-world-redesign-design.md)。

## 三方隔离

```text
world_generator/       私有、可执行的权威产生子
tasks/*/dataset/       纯数据格式入口，不引用代码
tasks/*/evaluator/     独立 reader、评分和在线隔离执行
```

产生子与 evaluator 不互相 import。公开 release 不包含 seed、生成参数、逐步 truth
或产生子。Agent world 只读挂载 `release/public`，不会挂载 release 根或 private。

## 本次单电子 release

本机运行时视图由以下命令建立；大波形不复制：

```bash
python benchmarks/JunoResBench/scripts/prepare_electron_single_site_release.py \
  --release /home/wujxy/mnt/lustrefs_juno26/users/lidian/jrb_v2/production/electron_single_site/release \
  --output benchmarks/JunoResBench/runtime/electron_single_site
```

Coding agent 和 Scientist 的共同 research world 位于：

```text
examples/junoresbench_electron_single_site_std_opt/
```

初始化和启动方法见该目录的 README。两种 agent 使用同一数据、baseline、评价器、
目标和只读边界，只有研究编排方式不同。

## 物理与发布证据

- [双档位设计报告](docs/JunoResBench_two_tier_design_report.md)
- [产生子物理与 1 MeV 分辨率预算](docs/generator_physics_and_resolution_budget.md)
- [能量守恒图](figures/electron_single_site_v2/energy_deposition_closure.png)
- [局域 quenching 图](figures/electron_single_site_v2/local_quenching.png)
- [可见能响应图](figures/electron_single_site_v2/energy_response.png)
- [电子径迹拓扑图](figures/electron_single_site_v2/track_topology.png)
- [probe/control 人口图](figures/electron_single_site_v2/probe_population.png)

## 新世界的真实 LPMT 几何

正式生产默认读取 JUNO J26.4.1 的 CD-LPMT 位置和类型表：

```bash
python benchmarks/JunoResBench/world_generator/build_task.py \
  --task ibd_positron_multisite \
  --out /path/to/candidate \
  --seed 20260903
```

产生子按 `CopyNo` 对齐两张表并 fail-closed。当前表包含 17,612 支 LPMT：
4,955 支 Hamamatsu、2,738 支 NNVT 和 9,919 支 HighQENNVT。发行数据公开位置、
CopyNo 和型号身份；逐管 PDE、gain、TTS 和 time offset 等私有响应常数不公开。
源文件 SHA-256、行数和型号计数写入发行 metadata，源 CSV 不复制进 git。

快速开发必须显式选择合成小几何，避免误把它用于正式生产：

```bash
python benchmarks/JunoResBench/world_generator/build_task.py \
  --geometry-mode uniform --n-pmt 128 \
  --task ibd_positron_multisite \
  --out /tmp/jrb-preflight \
  --seed 20260903 \
  --calibration-events-per-point 1 \
  --probe-events-per-point 1 --controls 64
```

真实坐标和型号身份只完成第一批结构升级；不同 PMT 型号的光学和波形响应属于
下一批，不能仅凭本批改动宣称 benchmark 难度已经提高。

图由 `scripts/plot_electron_single_site_release.py` 从冻结 truth 生成，不打开稀疏
波形样本文件，也不属于 agent 可见的任务包。

## v2 波形人工验收图

实际发行波形的 16 张位置、hit-pattern、时间和电子学检查图位于：

```text
figures/electron_single_site_v2/waveform_audit/
```

它们直接 mmap 挂载 release，只确定性抽取少量事件，不重新调用产生子，也不复制
波形。重画命令为：

```bash
python benchmarks/JunoResBench/scripts/plot_electron_single_site_waveforms.py \
  --release /home/wujxy/mnt/lustrefs_juno26/users/lidian/jrb_v2/production/electron_single_site/release \
  --output benchmarks/JunoResBench/figures/electron_single_site_v2/waveform_audit \
  --sample-limit 32
```

旧候选的 `6 ADC` ROI 阈值约等于电子学噪声的 `1.05 sigma`，导致绝大多数 ROI
合并成接近完整的 1000-sample 波形。它已被拒绝并保留为诊断证据。当前 release
使用 `29 ADC`（约 5 sigma），完整门禁通过；定量对比见设计报告第 12--13 节。

每个新 candidate 必须运行独立验收门禁：

```bash
python benchmarks/JunoResBench/world_generator/validate_release.py \
  --task electron_single_site \
  --release /path/to/candidate
```

门禁从已序列化的 public/private 文件生成 `validation_report.json`、带 16 张图的
`validation/README.md`，以及唯一的 `ACCEPTED` 或 `REJECTED` 标记。当前发行候选
见 [`validation/electron_single_site_current`](validation/electron_single_site_current)，
旧 `6 ADC` 候选的拒绝报告见
[`validation/electron_single_site_roi6_rejected`](validation/electron_single_site_roi6_rejected)。
旧门禁曾允许 deferred 重建可达性和难度；首次 agent 运行已经证明该规则不足。
替代发行必须同时通过物理门禁、盲测可达性和非平凡难度验证，且这些检查只评价
结果，不规定参与者使用任何算法。
