# JunoResBench

JunoResBench 是一个 JUNO-like 稀疏 PMT 波形重建 benchmark。当前版本只认
`world_generator/`、`tasks/` 和外部 release 数据三类彼此独立的组成部分。

## 当前任务

| 任务 | 输入 | 输出 | 验收 |
|---|---|---|---|
| [`tasks/electron_single_site`](tasks/electron_single_site) | 1--10 MeV 单电子稀疏波形 | `E_rec,x_rec,y_rec,z_rec` | `R_1MeV <= 3.0%` 且 1 MeV 顶点 RMS 不超过冻结门槛 |
| [`tasks/ibd_positron_multisite`](tasks/ibd_positron_multisite) | 正电子径迹与两条 511 keV gamma 的多点波形 | `E_rec` | `R_1MeV <= 3.0%` |

当前只生成了单电子 release。由于磁盘约束，十个 probe 能点各有 200 个事例，
另有 7680 个连续 control。该 release 不评价 t0。

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

图由 `scripts/plot_electron_single_site_release.py` 从冻结 truth 生成，不打开稀疏
波形样本文件，也不属于 agent 可见的任务包。
