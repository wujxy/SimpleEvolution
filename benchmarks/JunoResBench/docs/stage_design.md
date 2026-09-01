# JunoResBench — Stage 设计文档 v1

> **耦合 v2 stage 增量（2026-09，归档）**。v2 在本文所述 v1 stage 划分
> 上的增量；v1 各 stage 详设作为历史参考保留于下。
>
> - 新模块 `juno_res_bench/stopping_power.py`：ESTAR 形状合成 LAB 表
>   （5 keV–20 MeV，log-log 插值）、`birks_visible_mev`、
>   `charged_steps`（5% 分数细分、2 keV 截断）。
> - `DepositionSteps` 扩展：`kinetic_mev` / `dedx_mev_cm` /
>   `step_length_m` 与 `e_dep_mev` 对齐；`_Acc.deposit_charged_track`
>   逐步推进位置（cm→m 换算）、β 推进时间、小角扩散，出射能量记入
>   `e_escape_mev`。Compton 反冲/光电/次截断电子全部走同一 track 函数。
> - Stage 2：`rng.poisson(e_vis·ly)` 逐步产光；Cherenkov 均值 =
>   `step_length_m · cherenkov_photons_per_m · max(0, 1−cos²θ)`，
>   β 取步中点动能。
> - Stage 5 之后：`sparse_waveforms.encode_event`（阈值穿越 ROI 合并，
>   int16 残差 `adc − baseline`）→ `write_sparse_split` 流式目录
>   （metadata.json / index.npz / segment_samples.npy，memmap 读取）。
> - 数据角色：calibration（`role=−1`，13 位置 × 5 源能，标签只含
>   源能与部署位置）、probe（`role=0`，十点等数 Ek 网格）、control
> （`role=1`，[0,11] MeV 64 层分层均匀，保证评分 bin ≥100 事件）；
>   合并后洗牌隐藏顺序。
> - 评分模块 `resolution.py`：±2.5σ 三迭代峰拟合（≥100 事件）、
>   a/b/c 曲线拟合、64 bin 有效性闸门，`score_v2` 为唯一入口。

本文档是逐 stage 实现的依据。效应编号沿用 `effects.md`（A1..E10）。
原则：每 stage 物理边界单一、输入输出 schema 明确、独立可验证、
独立 RNG 流（互不干扰、回归测试稳定）。

---

## Stage 总览

```
EventInput(x,y,z,E_true,t0,direction,particle_type)
   │
Stage 1  Particle response   E_true → E_dep → E_vis
Stage 2  Photon generation   E_vis → photons {type, pos, dir, t_emit}   (SoA, 内存接口)
Stage 3  Optical response    photon → per-PMT 到达数 N_arrived_i + 到达时刻
Stage 4  Photon detection    N_arrived_i → N_pe_i (Binomial) + per-PE 时刻
Stage 5  PMT electronics     PE → charge → waveform (waverec snapshot)
Stage 6  Dataset & validation
```

---

## Stage 0 — Framework

### 模块

```
juno_res_bench/
  config.py          DetectorConfig（全部物理参数，见 effects.md §3）
  geometry.py        PMTLayout（uniform / juno csv）+ 方向格查找表
  stages/
    s1_response.py   Stage 1
    s2_photons.py    Stage 2
    s3_optics.py     Stage 3
    s4_detection.py  Stage 4
    s5_electronics.py Stage 5
  detector.py        DetectorSim: 串接 stage 1-5，产出 EventTruth
  truth.py           EventInput / EventTruth / PhotonSoA dataclass
```

### EventInput schema

| 字段 | 类型 | 说明 |
|---|---|---|
| x, y, z | float, m | 顶点 |
| E_true | float, MeV | 动能 |
| t0 | float, ns | 事例时刻 |
| direction | (3,) unit vec | **粒子初始方向**（v0 e-like 下各向同性抽样即可，字段必须存在） |
| particle_type | enum | `electron` / `gamma` / `positron`（v1 起全支持） |

### RNG 管理

```python
root = np.random.SeedSequence(seed)
rngs = [np.random.default_rng(s) for s in root.spawn(6)]
# rngs[0] Stage1, [1] Stage2(闪烁), [2] Stage2(Cherenkov), [3] Stage3, [4] Stage4, [5] Stage5
```

- 每 stage 独立流：stage 内加效应不影响其它 stage 的随机序列；
- stage 级单元测试可固定本 stage seed 独立复现；
- Cherenkov 单独成流：开关它不改变闪烁光序列；
- v1：γ 链/o-Ps 抽样用 `s1_response` 流（v0 闲置；e⁻ 路径仍零消耗，
  流被烧掉也不影响 e⁻ 输出——有回归测试锁定）。

### Truth schema（四层）

```
event 级:  x,y,z,E_true,E_dep,E_vis,dir(3),t0,
           particle_type, e_escape,               # v1
           n_gamma_scint, n_gamma_cher,          # 产生
           n_arrived, n_pe_produced, n_pe_total  # 到达/探测/入窗
step 级 (v1, ragged): pos(3), e_dep, e_vis, t, dir(3), kind
           （e⁻ = 单步特例；γ/e⁺ = Compton/湮灭链）
per-PMT:  pmt_ids, n_arrived_i, n_pe_i,
          pe_offsets
per-PE:   type(scint|cher), t_emit, t_tof, t_rel, q_pe,
          step_idx                                # v1, 指向 step 层
calibration truth (detector 级, 与事件无关):
          per-PMT pde_delta_i, gain_i, time_offset_i, tts_i
```

calibration truth 存于数据集（验证/标定研究用），**blind 包剥离**
（盲测 meta 中 seed 置 null）。

---

## Stage 1 — Particle response（A1, B3, B7；v1 起 A3/A4/A5）

- e⁻：`E_dep = E_true`（点沉积，全包含）；
  `E_vis = E_dep / (1 + kB·dE/dx)`，kB·dE/dx = 0.0241（默认开）。
  低能非线性 B7：`E_vis ×= nl_corr(E_vis)` 修正曲线（默认开，MeV 区 ~1%）。
  不消耗随机数。
- γ（v1，`stages/s1_particles.py`）：KN 链逐事件顺序模拟——
  λ(E)=1/(n_e·σ_KN(E)·(1+pe)) 自由程抽样，出球（R=nonuniform_radius_m）
  即逃逸；PE 分支概率 pe/(1+pe)、pe=(E_x/E)³；E<20 keV 就地吸收；
  Compton 散射角 KN 拒绝采样，沉积 ΔE 于作用点，反冲电子方向 =
  动量转移方向；γ 飞行时间按 c 累积（~2.5 ns/链）。
- e⁺（v1）：初级动能按 e⁻ 公式点沉积；一支均匀随机数定
  3γ(2.2%, o-Ps 延迟)/2γ-oPs(52.5%, 延迟 Exp(3.08ns))/2γ-prompt(45.5%)；
  2γ 背对背随机轴、3γ 单纯形均匀分裂；湮灭 γ 各自进 γ 链。
- 输出 `S1Output(e_dep, e_vis, steps=DepositionSteps, e_escape, particle_type)`；
  per-step `e_vis = quench(e_dep_step) × nl_corr(e_dep_step)`——e⁻ 单步
  与 v0 逐位一致，γ/e⁺ 低能步压制更强。
- RNG：γ/e⁺ 链用 `rngs["s1_response"]` 流（此前闲置，未新增 STAGE_KEYS，
  不扰动其他流）。抽取顺序固定：exponential(自由程) → uniform(PE 分支) →
  KN 拒绝对 → uniform(方位角)。

**锚点**：E_vis/E_true = 0.9764 ± 0（e⁻，确定性）；nl_corr 连续性；
能量守恒 Σstep_e_dep + e_escape = E_true(+1.022) ≤1e-6；
λ(1 MeV) ≈ 17 cm；e⁺/e⁻ 能标差 ≈0.5%；o-Ps 延迟分量 54.5%×Exp(3.08ns)。

---

## Stage 2 — Photon generation（B1, B2, B4, B5, B6 接口）

输出 PhotonSoA（numpy 结构化数组，**内存接口，不落盘**）：

```
photon_type: int8   (0=scint, 1=cherenkov)
pos:        (N,3) float32   # 点沉积 → 全部=顶点
dir:        (N,3) float32   # 单位方向
t_emit:     (N)   float32   # 发光时刻, ns, 相对 t0
```

### 闪烁分支（rng[1]）

- `N_γ ~ Gauss(E_vis·LY, √(E_vis·LY))`（B1/B2，LY=10168）；
- dir：各向同性均匀抽样；
- t_emit：4 指数混合 (4.6/15.1/76.1/397 ns @ .707/.205/.060/.028)（B5）。

### Cherenkov 分支（rng[2]）

- 光子数（Frank-Tamm 参数化，β 由动能 E 求）：
  `N_C = E_vis · LY_C · (1 − 1/β²(E))`，LY_C 校准使
  N_C/N_γ ≈ 2.5% @1 MeV、随 E 上升（1/β² 下降）；
- 方向：绕 `EventInput.direction` 的锥面，cosθ_C = 1/(nβ)，方位角均匀；
  **注意**：v0 输入方向各向同性抽样时锥无物理方向意义（framework 预留），
  文档与 truth 中保留 `direction` 以便方向重建任务扩展；
- t_emit = 0（prompt）。

**锚点**：σ(N_γ)/<N_γ> = 1/√N；N_C/N_γ @1MeV ∈ [2%,3%]；
dir 模长=1；锥角分布 peak 在 cosθ_C。

---

## Stage 3 — Optical response（C1-C5, C6, C9, C2/C3 时间弥散）

**只回答"光子到达哪个 PMT、何时到达"，不涉及 QE/PDE/CE。**

### 闪烁路径（解析权重，rng[3]）

```
w_i ∝ ε(r) · A_proj(cosθ_inc,i) / d_i²        (C1/C2/C6/C9 折叠入 ε(r), C4/C5 几何)
N_arrived ~ Multinomial(N_γ_scint, w)
```

- ε(r) = 1 + k₂(r/R)² + k₄(r/R)⁴（有效衰减+ESR+均匀阴影的校准合并）；
- cosθ_inc = −(photon_dir · n_inward)；A_proj = π(d_pmt/2)²·cosθ_inc；
- 覆盖率锚点：N·π(d/2)²/(4πR²) = 0.757。

### Cherenkov 路径（方案 A：射线求交，rng[3]）

逐光子（向量化，N_C ~250 @1MeV，量级小）：

1. 射线-球面求交：`ray(vtx, dir)` 与 R=19.365m 球面交点（解析公式）；
2. 交点方向 → 方向格（等立体角 bin，~0.5°）→ 最近 PMT（预计算查找表）；
3. 角距 < PMT 角半径（≈0.75°）→ 到达该 PMT；否则未到达（覆盖率的
   二进制体现；统计上等效，N_C 小所以噪声可接受）；
4. 存活因子：乘 ε(r)（与闪烁路径一致的顶点处有效衰减近似）。

### 到达时刻（两路径共用）

```
t_arrive = t_emit + d_path/(c/n) + Gauss(0, σ_scatter(d_path))
σ_scatter(d) = a_scatter · d        (C2 Rayleigh + C3 再发射的等效时间弥散,
                                     a_scatter ~0.02-0.05 ns/m, 实现时校准)
```

**锚点**：Σw=1；近/远 PMT pe 比 @r=15m ~60:1；Cherenkov 到达 PMT 的
角分布相对 track 方向呈现 θ_C 结构（未来方向任务的 sanity check）；
σ_scatter 合成后总时间残差 = TTS ⊕ scatter ≈ 4-4.5 ns。

---

## Stage 4 — Photon detection（D1-D5）

```
eff_i = QE_eff · CE(θ_inc,i) · (1 + δ_i)
N_pe_i ~ Binomial(N_arrived_i, eff_i)          (D4 探测统计)
```

- QE_eff：有效常数（p_det_center=0.1475 = 1500/10168 除以覆盖与 CE 平均，
  实现时由锚点反推归一）；
- CE(θ)：NNVT 9 点表线性插值（0°→90°: 1.0→0.73）（D2，**默认开**）；
- δ_i ~ N(0, 8%)：per-PMT PDE 离散，**per-detector 固定值**（D3，标定 truth）；
- per-PE 时刻：`t_hit = t_arrive + TTS`，TTS ~ Gauss(0, 4ns)（D5）；
  truth 保留 t_emit/t_tof 分解供检查。

**锚点**：<n_pe>/<n_arrived> = QE_eff·<CE·(1+δ)>；per-PMT 计数比与
w_i·eff_i 一致（χ² 检验）；δ_i 可从大样本标定恢复。

---

## Stage 5 — PMT electronics（E1-E6, E8, E9 接口, E3, E10 触发）

复用 `_vendor/wavegen_v1`（SPE 谱、log-normal 脉冲、FADC、白噪声）：

| 效应 | 实现 |
|---|---|
| E1 SPE 谱 | waverec `_sample_amplitudes`（Gauss 核心+Exp 尾） |
| E2 增益离散 | per-PMT `gain_i = 1+0.15·N(0,1)` 固定（标定 truth） |
| E3 afterpulses | **默认开**：per-PE prob 1.6%，延迟 ~Exp(µs 级)，幅度按 SPE 谱；入波形不入物理 truth |
| E4 dark | 24 kHz Poisson，全通道撒在 [t0−800, t0+1500]ns 扩展跨上（参与触发），窗内者入波形 |
| E5/E6 | waverec FADC + 噪声 |
| E8 窗截断 | 窗 = [t_trig−300, t_trig+700) ns；窗外物理/暗 PE 丢弃（truth 记 n_pe_total 仅入窗） |
| E10 触发 | **v4**：物理+暗 PE 时间 1ns 直方图，100ns 因果尾窗和首过 200pe = t_trig（搜索域 [t0±500]ns；纯暗 ~42pe/窗，阈下 >20σ）；绝对 t0 平移不变 → t0 按窗相对打分 |
| E9 堆积 | 接口：t0 列表重叠生成（默认单事例） |

**锚点**：单 PE 波形峰 ~7mV；dark 计数/窗 ~0.024/PMT；AP 窗内概率 ~0.3%/PE。

---

## Stage 6 — Dataset & validation

### 数据集分层

| 层 | 内容 | 用途 |
|---|---|---|
| scan | 事件级+per-PMT 计数, truth-only | 分辨率/非均匀性研究 |
| chain | + per-PE 数组 | 中间过程检查 |
| waveform | + 256 ch/事件 adc | 波形级检查 |
| benchmark | 正式冻结集 + blind 包 | agent 评测（后续） |

### 验证体系

- **stage 单元测试**（每 stage 独立 seed）：本文件各 stage 锚点；
- **全链锚点**（effects.md §4）：覆盖率/中心 pe/σ_E/E/时间残差/线性度；
- **回归**：同 seed 位级复现；跨版本锚点漂移检查；
- **图形**：chain_distributions / stages / timing / nonuniformity。

---

## 实现顺序与工作量估计

| 序 | stage | 主要工作 | 估计 |
|---|---|---|---|
| 1 | Stage 0 | truth schema 拆分、RNG 重构、目录调整 | 0.5d |
| 2 | Stage 1 | Birks/nl 默认开、分派表 | 0.5d |
| 3 | Stage 2 | PhotonSoA、Cherenkov 锥抽样、LY_C 校准 | 1d |
| 4 | Stage 3 | 权重重构、方向格查找表、射线求交、σ_scatter 校准 | 1.5d |
| 5 | Stage 4 | Binomial per-PMT、CE(θ) 表、δ_i 标定 truth | 0.5d |
| 6 | Stage 5 | afterpulses、标定 truth 接线 | 0.5d |
| 7 | Stage 6 | 单元测试、锚点脚本、数据集重生成、文档同步 | 1d |

每 stage 完成即跑该 stage 锚点 + 全链回归，再进下一 stage。
