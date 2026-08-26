# Trace 模式设计（stage 3 的逐光子输运模式）—— 已实现

双保真架构：`mode="fast"`（现有解析链路，扫描用）/ `optics_mode="trace"`
（向量化逐光子输运，benchmark 冻结数据集用）。两者之差 = 折叠近似
（ε(r)/σ_scatter/单波长）的可测量偏差。

## 实现状态（已验证）

| 项 | 结果 |
|---|---|
| 产额一致性 | trace 1478 vs fast 1473 pe @1 MeV（+0.4%，trace_det_norm=0.96）|
| 红移 | 到达 ⟨λ⟩=416.3 nm > 发射 412.3 nm（UV 被吸收+430nm 重发）|
| 传播拖尾 | mean TOF 141.6 ns（直飞 96.2），99% 分位 435 ns |
| Cherenkov 锥 | trace 输运后 median 47.5° vs θ_C 47.3° |
| 确定性 | 同 seed 位级复现 |
| 耗时 | 光学 ~19 ms/事件（DirectionGrid 查表），满足预期 |

**重要发现**：fast 模式的 σ_scatter=0.03 ns/m 严重低估传播拖尾——
真实的再发射（吸收点各向同性重发）+ Rayleigh 路径随机化给出 ~45 ns
的平均额外飞行时间和长尾（99% 分位 435 ns）。fast 模式的时序形状因此
偏窄，trace 模式才是物理真实的；benchmark 冻结数据集应使用 trace 模式。

## 光子态

```
pos (N,3)  dir (N,3 unit)  lam (N,) nm  t (N,) ns
counters: n_scatter, n_boundary, n_reemit
```

## 传播循环（向量化，按"代"推进活跃光子）

每代对全部活跃光子：

1. **竞争作用距离**（T1/T2/T3）：
   - d_abs ~ Exp(ABSLENGTH(λ))，d_sca ~ Exp(RAYLEIGH(λ))，
     d_bnd = 射线-球面求交（解析）
   - 取 min → 推进 pos，t += d_min·n/c
2. **吸收**（T1/T2）：若 rand < REEMISSIONPROB(λ)（≈0.8，荧光带内）：
   各向同性重发、t += 1.5 ns、λ ~ 荧光发射谱重采（红移）；否则死亡
3. **Rayleigh**（T3）：方向按 (1+cos²θ) 相函数偏转，λ 不变、无延迟
4. **边界**（T4/T5/T6）：命中方向在 PMT 角盘内 → 到达（记录 PMT、t、λ、
   入射角）；否则 rand < R_ESR(λ)≈0.96 → 漫反射（Lambert）继续；否则死亡
5. 循环至全部到达/死亡（上限 ~20 代）

## 光学表（toy 参数化，出处 JUNO-SW OpticalProperty.icc）

| 表 | toy 形式 | 依据 |
|---|---|---|
| 发射谱 | 430 nm 高斯核 σ=28 nm + 短波截止（PPO/bisMSB 复合） | JUNO 发射谱峰位/宽度 |
| ABSLENGTH(λ) | 100 m @>420 nm，短波幂律压低至 ~2 m @350 nm | LAB+fluor 有效吸收 |
| RAYLEIGH(λ) | 42 m × (430/λ)⁴ | JUNO: 42 m @430 nm |
| REEMISSIONPROB(λ) | 0.8 (λ<420 nm)，0 以上 | JUNO 平台值 0.80 |
| R_ESR(λ) | 0.96 | JUNO ESR 表 |
| QE_rel(λ) | NNVT QE_shape 归一（探测端乘入 p_det） | PMTSim QE 表 |

## 探测端衔接

到达光子携带 (pmt_idx, t_arrive, λ, cosθ_inc)。stage 4 不变：
CE(θ_inc) × pde_delta × p_det(λ) —— p_det(λ) = p_det_center ×
QE_rel(λ)/QE_rel(λ_ref)，绝对归一仍由中心 pe 锚点锁定。

## 明确不做

偏振、荧光能量转移微细节（有效表替代）、acrylic/水多区域+TIR
（折叠入 CE(θ) 有效表，v2）、闪烁 rise time、ESR 角分辨微结构、
磁场效应。

## 锚点（trace 模式）

| 观测量 | 期望 |
|---|---|
| 中心 pe @1 MeV | 与 fast 模式一致（p_det 归一锁定）|
| 到达率 | ≈ coverage 0.757 × 存活率（吸收损失 ~20%）|
| 时间残差 | TTS ⊕ scatter(自然涌现) ⊕ 再发射延迟 ⊕ offset |
| 发射-到达 λ 分布 | 红移（短波被吸收+重发）|
| 边缘/中心均匀性 | 涌现值 vs fast 模式 ε(r) 的差 = 折叠近似偏差 |
| Cherenkov 到达锥 | 不变（已 trace）|

## RNG

新流 `s3_trace`（spawn 键追加）；trace 模式启用时不扰动
s2/s4/s5 及 fast 模式的任何抽样。Cherenkov 射线部分沿用 s3_optics
现有实现（同一流内 scint-trace 之后）。

## 预期性能

10⁴ 光子 × ~3 线段/光子的向量化运算 ≈ 5–15 ms/事件（对比：s5 波形
合成 ~60 ms）。GPU 不需要；波形合成才是后续优化点。
