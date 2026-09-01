# JunoResBench Toy MC 与 JUNO-SW full MC 的差别

> **基准 v1 → v2 差异总表（2026-09，现行）**。下表是基准 v2 相对本文
> 所述 v1 世界的增量；v2 之下的逐环节对照仍以本文为历史参考。
>
> | 环节 | v1 | v2 |
> |---|---|---|
> | 任务 | E/vertex/t0 三输出，npz 批量预测 | 单输出 E_rec，在线逐事件 Submission 合约 |
> | 事件 | electron/mixed（单点沉积） | IBD 类正电子（多步输运 + 2×511 keV 湮灭 γ） |
> | Stage 1 | 单点沉积 + 事件级 quench/nl | `charged_steps` 多步输运 + 局部 Birks，无 nl 钩子 |
> | Stage 2 | 正态取整产光；Cherenkov 按 E_dep | 逐步 Poisson；Cherenkov 按步长 × (1−cos²θ) |
> | Stage 3–5 | trace 光学 + SPE/电子学 | 不变（waverec 快照沿用） |
> | 评分 | E 分辨率/线性 + 顶点 + 时间 | JUNO 曲线 R@1MeV ≤ 3.0% 唯一判据 + 连续控制样本有效性闸门 |
> | 数据 | dense adc npz，train/val/test | 稀疏 ROI 流式容器；calibration（role=−1，带标签）/probe（0）/control（1）分层公私分离 |
> | 产生子 | 白盒 track 随附源码 | 公开包不含产生子与参数 |
>
> 细节见 `stopping_power.py`、`sparse_waveforms.py`、`resolution.py`
> 模块文档与 `scripts/make_v2_benchmark.py`。

对照 `Simulation/DetSimV2`（探测器）、`Simulation/SimSvc/PMTSimParamSvc`
（PMT 参数）、`Simulation/ElecSimV3`（电子学）逐环节说明：toy MC 做了什么、
JUNO-SW 怎么做、差别的后果。取舍理由见 [`effects.md`](effects.md)。

## 0. 总体

| | JUNO-SW full MC | JunoResBench toy MC |
|---|---|---|
| 输运引擎 | Geant4 逐粒子/逐光子输运（可选 Opticks GPU） | 纯 numpy 解析/抽样模型 |
| 单事件光子数 | ~10⁴ 根光学光子逐一追踪 | 不追踪光子，只抽 N_pe |
| 事件生成速率 | ~Hz–kHz 级（全链路） | 目标 ≫1 kHz/core |
| 事件输入 | generator (Geant4 vertex) | 直接给定 (x,y,z,E_true,t0)，无粒子输运 |
| 用途 | 生产级 MC 生产 | AI agent reconstruction benchmark |

## 1. 能量沉积 (E_true → E_dep → E_vis)

| 环节 | JUNO-SW | Toy MC | 后果 |
|---|---|---|---|
| e± 输运 | Livermore EM 逐步沉积 | e⁻ 不做（点状、全沉积，E_dep=E_true）；e⁺ 初级动能同样点沉积 | e⁻ 无 dE/dx 涨落、无 delta ray，能量分辨略乐观；e⁺ 初级沉积的 <0.5% dE/dx 差异并入共享 quench 常数 |
| γ | 完整 Compton/photoelectric 链，多作用点+escape | **v1 已实现**：逐 γ KN 链（拒绝采样）+ PE 终止 (E_x/E)³ + 出球逃逸；λ=1/(n_e·σ_KN) | 无 δ-ray/多重散射细节；多作用点+顶点弥散+链飞行时间(~2.5ns@1.5MeV) 进入链路 |
| e⁺ | positronium 形成(54.5%)+3γ 分支(2.2%) | **v1 已实现**：o-Ps Exp(3.08ns) 延迟、3γ 单纯形分裂（动量守恒玩具近似）、2×511 背对背，湮灭 γ 各自进 γ 链 | o-Ps 分支/时序/能标与 JUNO 数对齐；3γ 方向独立各向同性（文档化近似） |
| Birks quenching | 逐步 E/(1+kB₁δ+kB₂δ²)，按粒子选 kB | 同一解析因子 per-step 应用：e_vis_step = e_dep/(1+kB·dE/dx) × nl_corr(e_dep_step) | e⁻ 单步 = v0 逐位不变；γ/e⁺ 低能次级电子的额外压制经 B7 曲线自然涌现（e⁺/e⁻ 能标差 ~0.5-1%） |

## 2. 光子产生 (E_vis → N_gamma)

| 环节 | JUNO-SW | Toy MC |
|---|---|---|
| 光产额 | LY=10168 ph/MeV 逐步产生，σ=√N 涨落 | 合并为 N_pe ~ Poisson(E·μ_pe(r))——**不区分 N_gamma 和探测效率两个二项式步骤**，直接一步 Poisson（数学上 thinning 可合并） |
| 时间谱 | 4 成分逐光子抽样，α/n 各自独立表 | 同一 4(或2)成分表用于一切 e-like 事件；无 PSD |
| Cherenkov | modified G4 过程，factor 0.517，与闪烁耦合再发射 | 可选 prompt 小分量，无光谱、无角锥关联细节 |

## 3. 光学输运 (N_gamma → N_gamma@PMT)

| 环节 | JUNO-SW | Toy MC |
|---|---|---|
| 吸收/Rayleigh/再发射 | 波长相关查表逐光子输运 | 折叠进 ε(r)：有效衰减进入径向不均匀函数 |
| 表面（ESR 膜/acrylic/oil/water） | 表面过程逐次处理 | 不模拟；其净效果（反射增益等）吸收进 ε₀ 归一化常数 |
| 几何覆盖 | 17612 支 PMT 逐支摆放 + mask volume | 解析覆盖系数（或简化 PMT 环带布局），覆盖率 ~75% 进 ε₀ |
| 光谱依赖 | QE(λ)、发射谱、Rayleigh λ⁻⁴ | 单波长等效，无光谱维度 |

## 4. 光子探测 (→ N_pe)

| 环节 | JUNO-SW | Toy MC |
|---|---|---|
| QE | 波长曲线 × per-PMT PDE@420nm 归一（11–41% 离散） | 单一有效 PDE 常数；per-PMT PDE 离散折叠进 ε(r)（v1 可加离散） |
| CE(θ) 角响应 | 9 点样条 / Fresnel 薄膜模型 | 简化 g(cosθ_local) 或并入 ε 的角向项 |
| PMT 数量/类型 | LPMT(NNVT+Hamamatsu 混合) + SPMT(3") | 单一类型（NNVT MCP 参数），数量可配 |

## 5. PMT/电子学 (→ waveform)

| 环节 | JUNO-SW | Toy MC（= waverec） |
|---|---|---|
| SPE 电荷 | per-PMT 测量直方图 / MCP gamma 模型 / θ 相关尾权重 | 单一参数化谱（Gauss 核心 + Exp 尾），固定尾权重 |
| gain/TTS/offset | DB 中 per-PMT 实测值 | per-channel 高斯 spread |
| dark noise | per-PMT DCR from DB | 统一 24 kHz |
| afterpulse/prepulse | AP 联合直方图采样 / prepulse 未生成 | 都不做 |
| 读出 | 触发链定义窗口、event mixing、双增益、饱和、overshoot、真实波形模板 | **v4 起有触发**：全局 PE 率滑窗（100ns 因果尾窗、200pe 阈）定窗 = [t_trig−300, +700)ns；无 event mixing/双增益/饱和，log-normal 解析脉冲 |

## 6. 对评价结论的影响（读 benchmark 结果时须知）

- Toy MC 的能量分辨底线（~2.6%/√E）由显式构造保证，接近但**不等于** JUNO
  真实值；绝对数值不可直接与 JUNO 论文对比，只能做 benchmark 内相对比较。
- 缺少 step-level 沉积涨落和光谱效应 → toy 分辨率系统性偏乐观；
  agent 在 toy 上达到的分辨是其在给定效应集下的下界。
- 位置-能量耦合只来自参数化的 μ_pe(r)，其具体形状是构造的（多项式），
  agent 若过拟合该形状不算作弊——标定 detector response 本身就是任务的一部分。

## 7. 中间量输出约定（truth 层级）

数据集中保存完整中间链条，便于检查每级分布（npz 实际键名，
`evt_` 前缀 = 事件级；ragged 级由对应 `*_offsets` int64 (N+1) 索引）：

```
per event:  evt_x_m/y_m/z_m, evt_e_true, evt_e_dep, evt_e_vis, evt_t0,
            evt_t_trigger   (全局触发时刻；打分口径 t0_ref = evt_t0 − (evt_t_trigger − 300))
            evt_e_escaped   (逃逸 γ 带走的能量；e⁻ 恒 0)
            evt_e_scored    (打分基准：e⁺ = e_true+1.022，其余 = e_true)
            evt_particle_type (int8: 0=e⁻ 1=γ 2=e⁺)
            evt_n_gamma      (= Σ per-step 闪烁光子)
            evt_n_pe_produced / evt_n_pe_total
            evt_n_steps
per step:   step_pos (M,3), step_e_dep, step_e_vis, step_t_ns,
            step_dir (M,3), step_kind (int8: 0 primary / 1,2 主γ Compton,PE /
            3,4 湮灭γ Compton,PE / 5 sub-cutoff)
per PMT:    pmt_ids, n_pe_pmt
per PE:     t_emit_ns, t_tof_ns, t_rel_ns, q_pe, pe_step (int32, 指向 step 层)
waveform:   adc (uint16 行), adc_pmt_ids
```

其中 `n_gamma → n_pe` 之间不再有独立随机数（合并 thinning），但
`n_gamma` 作为闪烁分支实际抽样数保留。e⁻ 事件是单步特例
（step 数=1、kind=0、t=0、位置=顶点），与 v0 语义逐位一致。
盲测 test.npz 只保留 `adc/adc_pmt_ids/pmt_offsets/meta`，
且 meta 中 seed 置 null（防用种子复现测试真相）。
