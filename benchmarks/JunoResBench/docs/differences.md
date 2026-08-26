# JunoResBench Toy MC 与 JUNO-SW full MC 的差别

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
| e± 输运 | Livermore EM 逐步沉积 | **不做**：e-like 事件假设点状、全沉积，E_dep=E_true | 无 dE/dx 涨落、无 delta ray；能量分辨略乐观 |
| γ | 完整 Compton/photoelectric 链，多作用点+escape | v1 参数化：指数作用距离 + 有限次散射 + escape 概率 | v0 不支持 γ 事件 |
| e⁺ | positronium 形成(54.5%)+3γ 分支(2.2%) | v1 参数化：o-Ps 延迟 + 能量丢失分支 | v0 e⁺=e⁻ 处理 |
| Birks quenching | 逐步 E/(1+kB₁δ+kB₂δ²)，按粒子选 kB | 全局解析因子 quench(E) 或关闭 | 无 step-level 涨落；非线性是确定性的 |

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
| 读出 | 触发链定义窗口、event mixing、双增益、饱和、overshoot、真实波形模板 | 固定窗口无条件读出、单增益、log-normal 解析脉冲 |

## 6. 对评价结论的影响（读 benchmark 结果时须知）

- Toy MC 的能量分辨底线（~2.6%/√E）由显式构造保证，接近但**不等于** JUNO
  真实值；绝对数值不可直接与 JUNO 论文对比，只能做 benchmark 内相对比较。
- 缺少 step-level 沉积涨落和光谱效应 → toy 分辨率系统性偏乐观；
  agent 在 toy 上达到的分辨是其在给定效应集下的下界。
- 位置-能量耦合只来自参数化的 μ_pe(r)，其具体形状是构造的（多项式），
  agent 若过拟合该形状不算作弊——标定 detector response 本身就是任务的一部分。

## 7. 中间量输出约定（truth 层级）

数据集中保存完整中间链条，便于检查每级分布：

```
per event:  E_true, E_dep, E_vis, t0, x, y, z
            n_gamma        (= E_vis × LY_scint，产生端光子数)
            n_pe_total     (= Σ_pmt n_pe_pmt，探测端 pe 总数)
per PMT:    pmt_id, n_pe_pmt, hit times[], spe charges[]
per PE:     t_emit(闪烁发光时刻), t_hit(PMT 处到达时刻), q(SPE 电荷)
```

其中 `n_gamma → n_pe` 之间不再有独立随机数（合并 thinning），但
`n_gamma = round(E_vis·LY)` 作为确定性记账量保留，供画分布用。
