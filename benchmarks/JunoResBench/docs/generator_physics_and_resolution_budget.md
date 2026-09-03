# JunoResBench 产生子：物理链路与分辨率预算

本文是 JunoResBench 两档位题库的物理建模说明。它回答两个不同的问题：

1. 产生子保留的效应是否足以让任务成为一个合理的 JUNO-like 波形重建问题；
2. 为什么在这个 synthetic world 中把 `R_1MeV <= 3.0%` 设为长期目标是合理的。

这不是对真实 JUNO 探测器性能的宣称，也不是一次已完成的 MC 测量。所有最终
数值仍必须由 HTCondor 上冻结配置的独立 release 生成和验证。

## 1. 从粒子到稀疏波形

隐藏产生子按下列因果链工作，而不是把一个总能量直接乘以全局 scale：

```text
初级粒子
  -> 局部能量沉积（带电径迹 / gamma 相互作用链）
  -> 每个沉积点的 Birks 可见能量、闪烁光和 Cherenkov 光
  -> 逐光子光学输运与 PMT 探测
  -> SPE 脉冲、暗噪声、后脉冲、触发、ADC
  -> 零抑制稀疏波形
```

因此，位置、沉积拓扑、低能非线性、光子到达时间和电荷统计都保留在观测量中；
agent 只能从公开的几何、标定数据和波形中反推它们。

## 2. 三类粒子的局部物理

| 初级/次级粒子 | 当前实现 | 物理依据与保留的可观测后果 |
|---|---|---|
| 电子 | 采用有机液闪中公开电子 stopping-power 曲线形状的紧凑表，按连续慢化近似逐步损失动能；步长由 `dE/dx` 换算。每一步独立施加 Birks，再独立抽样发光。 | `dE/dx` 随低能显著上升，故同样的能量不能再用一个全能区常数可见度处理。径迹在 1--10 MeV 虽短但不是单点，逐步位置决定光收集图样和时间。 |
| gamma | 以电子密度和 Klein--Nishina 总截面抽样相互作用长度；Compton 散射抽样出反冲电子，参数化光电分支在低能增强，低于阈值时吸收；所有反冲/光电电子回到电子输运。可穿出边界。 | 511 keV gamma 的能量通常分散在多个 Compton/吸收点，且靠边事件会有逃逸；这正是 IBD-like 档中能量--位置--拓扑耦合的来源。 |
| 正电子 | 初始动能走与电子相同的局部带电输运；停下后严格产生两条背对背的 511 keV gamma，并分别走 gamma 链。题库配置关闭三 gamma 分支。 | 可见能量来自正电子动能沉积加两个独立的 annihilation-gamma 多点链。共享 e+/e- stopping-power 表是有意的、受控的近似：其差异低于本 benchmark 保留的精度，不能把任务复杂度花在该细节上。 |

### 局部 Birks quenching 是关键而非装饰

带电粒子每一局部沉积步使用

```math
E_{\rm vis,step}=\frac{\Delta E}{1+k_B(dE/dx)},\qquad
k_B=0.012\ {\rm cm/MeV}.
```

产生子采用的合成液闪 `dE/dx` 表在 1 MeV 约为 `1.48 MeV/cm`，此时因子约
为 `0.983`；在 20 keV 约为 `8.8 MeV/cm`，约为 `0.904`；在 5 keV 约为
`31 MeV/cm`，约为 `0.729`。这给出预期的低能下压：gamma 产生的低能反冲
电子与单一 1 MeV 电子不会有相同的可见响应。它替换了旧版“整事件统一 scale”
这种物理上不成立的做法。

stopping-power 表是 **LAB-like、ESTAR-shaped 的 synthetic material 定义**，
不是 JUNO 的材料数据库；其用途是保留正确的能量依赖和局部响应，而非声称
Geant4 级材料精度。

## 3. 光、光学与电子学

### 几何升级边界

替代世界的正式生产使用 JUNO J26.4.1 CD-LPMT 位置与型号表，不再使用完全旋转
对称的 Fibonacci 球面。位置表和型号表按 CopyNo 严格对齐，当前组成是 4,955 支
Hamamatsu、2,738 支 NNVT 和 9,919 支 HighQENNVT。公开几何包含这些可观察身份，
但不包含产生子抽样的逐管响应真值。

逐管性能不是从 JUNOSW 的 PMT 参数库复制。产生子按固定 seed，从下述公开总体
约束生成一套私有、可重生的合成探测器；真实型号身份只决定抽样总体。

### PMT 响应的来源与建模边界

公开锚点来自 JUNO Collaboration 的 20-inch PMT mass-characterization 论文
（Eur. Phys. J. C 82, 1168, 2022，DOI:
`10.1140/epjc/s10052-022-11002-8`）。产生子不读取论文所述但未公开发布的逐管
数据库，也不读取 J26.4.1 的 `PMTParam*.root`、波形模板或运行标定文件。

| 量 | 公开测量锚点 | 产生子实现 | 性质 |
|---|---|---|---|
| PDE | HPK 28.5%，low-QE NNVT 27.3%，high-QE NNVT 31.3% | 以三类均值和窄 log-normal 管间散布抽样，再整体归一到中心 PE yield | 均值为公开测量；散布宽度为建模选择 |
| DCR | bare HPK/NNVT 约 15.3/49.3 kHz；封装 NNVT 约 31 kHz | HPK 15.3 kHz、两类 NNVT 31 kHz 的 log-normal 逐管总体 | 均值为公开测量；总体形状/宽度为建模选择 |
| TTS | HPK 约 1.3 ns、NNVT 约 7.0 ns（均为 sigma） | HPK 高斯；NNVT 用对称核心加卫星峰，保持零均值与目标 RMS | RMS 为公开测量；多峰参数为受论文典型 TT 图约束的近似 |
| SPE 电荷 | 分辨率 HPK 27.9%、NNVT 33.2%；NNVT 谱有非高斯长尾 | HPK 截断高斯，NNVT 单位均值 log-normal | RMS 为公开测量；解析分布族为建模选择 |
| gain | 工作点为 `1e7`，测量增益分布宽约 2--4% | 单位化增益，逐管 sigma 4% | 公开量级约束下的标定坐标 |
| time offset | 逐管需标定，但公开论文没有发布 CD 全体数值 | HPK/NNVT 零均值 sigma 0.8/1.2 ns | 明确的建模选择，不冒充测量 |

High-QE NNVT 与普通 NNVT 只在有公开分组结果的 PDE 上分开；DCR、TTS 和 SPE
形状暂共用 NNVT 响应族。论文给出的 after-pulse 是指定时间窗和约 100 p.e.
初始脉冲下的**电荷比**，不是逐 PE 发生概率，因而不能直接拿来替换产生子的
afterpulse probability。

| 层级 | 产生子中的效应 | 为什么需要它 |
|---|---|---|
| 发光 | 闪烁光按局部可见能量 Poisson 抽样，中心标定为 `10168 photons/MeV` 和有效 `1500 detected PE/MeV`；四分量闪烁时间常数为 4.6、15.1、76.1、397.0 ns。 | 给出光子计数涨落，并保留早/晚光的顶点信息。 |
| Cherenkov | 对每个超过阈值的带电步按 Frank--Tamm 型 `1-1/(n beta)^2` 与路径长度抽样；1 MeV 电子的量级约为闪烁光的 2.5%。 | 是小但有方向性和早到时序的信息源，不能用一个总电荷模型完全替代。 |
| 光学输运 | trace 模式逐光子处理波长依赖吸收、再发射及其延迟、Rayleigh 散射、PMT 圆盘命中和 ESR 漫反射；探测端还有波长 QE、入射角 collection efficiency 与位置非均匀性。 | 多点沉积、边缘事件和时间残差由这一层变为可观测差异。trace normalization 只用于保持中心 PE 标定锚点，不抹掉位置/时间效应。 |
| PMT/电子学 | 按 HPK/NNVT/High-QE NNVT 抽样逐管 PDE、DCR、gain、time offset；HPK 与 NNVT 使用不同的 SPE 电荷族和 TTS 形状；另含后脉冲、触发定义的 1 us 窗口、1 GHz/14 bit ADC、白噪声和零抑制。 | 同一光子在不同管上不再得到可交换响应；charge、time 与 occupancy 提供互补且失配的观测，迫使重建处理标定和似然形状。 |

逐管响应是合成潜变量，不对应任何真实 JUNO PMT。这样保留公开测量支持的型号差异
和统计难度，同时避免把 JUNOSW 的非公开/运行相关性能常数伪装成 benchmark 自有
数据。光学参数同样用于维持正确的效应层级和可辨识结构，不替代 JUNO-SW/Geant4
的完整工程仿真。

## 4. 1 MeV 分辨率的先验预算

对经过位置和非线性校正的能量估计器，常用的诊断参数化是

```math
\frac{\sigma_E}{E}=
\sqrt{\frac{a^2}{E/{\rm MeV}}+b^2+\frac{c^2}{(E/{\rm MeV})^2}}.
```

`a` 是光子/光电子统计与 SPE 统计，`b` 是残余空间非均匀性、响应非线性和标定
误差，`c` 是电子学噪声、阈值、基线等近似加性项。评分使用同型曲线，但具体的
`a,b,c` 只能由冻结 release 的重建结果拟合，不能由配置常数直接宣布。

最干净的下限来自中心处已探测到的光电子数：

```math
N_{\rm pe}(1\,MeV)=1500,
\qquad
\left(\frac{\sigma_E}{E}\right)_{\rm Poisson}
=\frac{1}{\sqrt{1500}}=2.58\%.
```

这比从 `10168 photons/MeV` 得到的 0.99% 更相关：后者只计算光子产生，前者已
包含覆盖率、QE、collection efficiency 和平均衰减后的实际检测统计。3.0% 目标
相对纯 PE 泊松极限还允许的正交 RMS 预算为

```math
\sqrt{(3.0\%)^2-(2.58\%)^2}=1.53\%.
```

所以 3.0% 不是一个松散的“跑通即可”线：朴素总电荷加全局比例通常仍会受到位置
非均匀性、局部 quenching、gamma 逃逸、SPE/噪声与波形提取误差影响；但该目标也
没有要求不可验证的理想零涨落极限。它要求 agent 长时间优化一个绝对能标下的、
物理校正后的重建器。

对单电子档的顶点阈值，private generator 使用 **理想 per-PMT charge pattern**
的 Poisson Fisher information 计算 CRLB，再取 `1.15 x CRLB` 并向上取整到
0.1 cm。它是 charge-pattern 的乐观锚点，而非完整波形顶点信息的理论极限：真实
波形中的时间信息可以补充空间信息，电子学与模型失配则会损失信息。最终冻结阈值
必须写入 release 的 `evaluation_config.json`。

## 5. 有意的简化、边界与发布验证

本题库追求的是“足够完备的隐藏世界”，不是把实验软件搬进 benchmark。下列简化
是显式的：

| 简化 | 不保留什么 | 为什么仍可接受 |
|---|---|---|
| 连续慢化 + 合成 stopping-power 表 | delta rays、完整材料/原子壳层数据库、e+/e- 的微小 stopping-power 差异 | 保留决定 quenching 的低能 `dE/dx` 结构，并把不可辨识的材料细节排除出任务。 |
| 参数化 gamma 光电分支 | 原子结合能、Doppler 展宽及完整次级级联 | 保留 Compton 多点、低能电子和边界逃逸这三个重建主导效应。 |
| 简化的 PMT/电子学族 | 双增益链、饱和、run-by-run 漂移和 pile-up | 对 1--10 MeV 单事件波形，保留直接影响 charge/time extraction 的统计、暗噪声、后脉冲、触发和 ADC。 |
| 有限光学 trace | 有限迭代数和参数化光学表 | 逐光子保留吸收、散射、再发射和边界反射，避免退化为只按距离缩放总光量。 |

发布时不能只看代码说明，必须在 HTCondor 真实生成后完成以下闭环：能量守恒；低能
quenching 单调下压；正电子严格两条 511 keV gamma 的湮灭能量；固定 seed 重生 hash；
公开 baseline 与私有 reviewed reference；以及 probe 的 bootstrap 稳定性。只有这些
检查通过，解析预算才有资格作为题库参数的物理解释。

相关设计和发布流程见 [双档位设计报告](JunoResBench_two_tier_design_report.md)。
