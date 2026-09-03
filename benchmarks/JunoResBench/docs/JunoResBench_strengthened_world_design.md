# JunoResBench 加强版替代世界与验收设计

日期：2026-09-03

状态：已完成讨论，待用户审阅

适用范围：单电子档及后续多 gamma/IBD-like positron 档

## 1. 背景与设计目标

JunoResBench 第一轮单电子题中，事件总 ADC 积分经过线性标定即可轻松达到 1 MeV
能量分辨率目标。问题不在参赛 agent 使用了不合理方法，而在替代世界把

```math
E_{\rm true}\rightarrow E_{\rm vis}\rightarrow N_\gamma
\rightarrow N_{\rm PE}\rightarrow Q
```

压缩成了接近一维、近似线性的映射。位置依赖接近球对称，波形中的时间和形状信息
也没有带来足够的条件信息。因此题目既没有要求 agent 建立完整探测器响应模型，也
不能区分总电荷拟合、逐 PMT 电荷似然、时间似然和联合波形模型。

加强版的研究对象是

```math
P\!\left(\{\mathrm{ADC}_i[n]\}\mid E,\mathbf{x}\right),
```

而不是人为构造更复杂的确定函数 `Q=f(E)`。设计目标如下：

1. 产生子包含实际影响 ADC 条件分布的主要因果过程；
2. 难度来自可学习的电荷--时间--位置--通道相关性，而非任意增大噪声；
3. 公开数据能支持通道标定、能量标定和位置标定；
4. agent 逐事件重建 `E, x, y, z`，不限制算法路线；
5. evaluator 公开评分逻辑但隐藏正式测试 truth；
6. generator、dataset、evaluator 三者绝对独立，互不引用；
7. 正式生产前同时证明 3% 目标在信息上可达、简单统计量又不能达到；
8. 每次 release 都附带无需 agent 才能阅读的物理与数据质量验收报告。

实现无需复制 JUNOSW。公开 JUNO 几何可用于 PMT 位置和类型；性能参数只以公开
论文和测量结果约束合成分布，不直接读取 JUNOSW 的逐管性能常数。

本文以 2026-09-03 的联合讨论结论为准，取代先前设计中“开发集直接公开逐事件
truth”“只有能量分辨率一个正式指标”“必须先由专家重建达到 3% 才能发行”和
“IBD 档先于单电子档实现”等不再成立的要求。先前文档仍可作为决策历史保留。

## 2. 两个题目档位

### 2.1 单电子档

- 粒子：单个电子；
- 初始动能：1--10 MeV；
- 顶点：液闪有效体积内连续分布；
- 输出：逐事件 `E_rec, x_rec, y_rec, z_rec`；
- headline：1 MeV 能量分辨率不高于 3.0%。

真实 JUNO 没有能在全体积任意位置释放 1--10 MeV 单电子的常规刻度源，因此本档
必须诚实命名为 **idealized mono-electron calibration challenge**。公开刻度样本
只覆盖离散能量和稀疏位置，隐藏测试覆盖连续能量和连续位置。

### 2.2 多 gamma/IBD-like positron 档

公开部分采用实际可部署刻度源语义：标称源能量、衰变谱和部署位置已知，但逐事件
沉积结构未知。隐藏物理样本采用 IBD-like positron：正电子能损、湮灭及两个
511 keV gamma 的多点沉积共同决定波形。该档检验从刻度源到不同物理拓扑的响应
迁移能力。

两个档位共享探测器和数据接口，但分开发布、分开评价。加强工作先在单电子档闭合，
再迁移到多 gamma 档。

## 3. 权威产生链

权威实现采用逐沉积、逐光子、逐 PMT 和逐采样的因果产生链：

```math
(E,p,\mathbf{x},\mathbf{d})
\rightarrow
\{\Delta E_k,\mathbf{x}_k,t_k,(dE/dx)_k\}
\rightarrow
\{\gamma_j:\lambda_j,\mathbf{n}_j,t_j,\text{type}_j\}
\rightarrow
\{\mathrm{PE}_{ij},q_{ij},t_{ij}\}
\rightarrow
\{V_i(t)\}
\rightarrow
\{\mathrm{ADC}_i[n]\}.
```

预计算响应 kernel 可在以后作为经过逐项验证的性能优化，但不能成为物理定义。正式
产生子必须保留足够的内部审计 truth，使每个阶段都能单独验收。这些 truth 不进入
公开题库。

## 4. 第一闭环：光子产生、传播与探测

### 4.1 粒子沉积与发光

电子按路径 step 产生局部能量沉积，逐 step 使用 stopping power 和 Birks 关系，
不能对整个事件只乘一个 quenching scale。scintillation 光子数由局部可见能量和
计数涨落产生；发光时间使用多分量时间谱。

Cherenkov 光子与 scintillation 光子从产生时即分开。Cherenkov 波长按

```math
\frac{d^2N}{dx\,d\lambda}\propto
\frac{1}{\lambda^2}
\left(1-\frac{1}{\beta^2n^2(\lambda)}\right)
```

产生，并以同一 `n(lambda)` 决定阈值和锥角。两类光不能共用同一发射光谱。

### 4.2 光子状态

每个活跃光子至少携带：

```text
(position, direction, wavelength, time, photon_type, medium, alive)
```

传播后必须保留最终方向和入射角，不能在 PMT 探测阶段重新用沉积点--PMT 弦方向
替代。吸收后若发生再发射，生成新波长、各向同性方向和发光延迟，并保留其来源标签
供私有 QA 使用。

介质内传播同时竞争吸收、Rayleigh 散射和下一个几何边界。传播时间使用群折射率

```math
n_g(\lambda)=n(\lambda)-\lambda\frac{dn}{d\lambda}.
```

### 4.3 PMT 光学探测

光子到达 PMT 表面时保留波长、最终方向、到达时间、入射角、光阴极落点和路径历史。
探测概率采用

```math
P_{\rm det}=
A_i\,QE_{\rm type}(\lambda)\,
CE_{\rm type}(\theta_{\rm hit},\phi_{\rm hit})\,
\epsilon_{\rm optical}(\lambda,\theta_{\rm inc}).
```

NNVT/MCP 与 HPK/dynode 使用不同的公开曲线约束分布。未探测光子并非总是死亡，
而按波长、入射角和 PMT 类型抽样反射或吸收。

## 5. 第二闭环：多介质边界、结构与 PMT 反射

最小物理完整几何为

```math
\mathrm{LS}\rightarrow\mathrm{acrylic}\rightarrow
\mathrm{water}\rightarrow\mathrm{PMT}.
```

液闪球、约 12 cm 亚克力层和水缓冲区使用公开 JUNO 尺度作为锚点。每种介质分别
定义波长相关折射率和吸收长度；只在物理需要时加入散射。

光子到达介质边界后执行 Snell 定律、非偏振 Fresnel 抽样和全反射。反射光继续在
原介质传播，透射光进入下一介质。实现使用解析球面求交，不引入完整 CAD 或网格
追踪。

固定结构采用少量解析遮挡体近似，包括 chimney、acrylic nodes、光学 mask、PMT
保护结构和钢结构角向遮挡带。结构固定在 detector 坐标系中，遮挡由实际射线路径
决定；不得逐事件随机生成响应斑点。公开资料不足的尺寸必须标注为“公开几何约束
下的合成实现”。

PMT 表面满足

```math
P_{\rm detect}+P_{\rm reflect}+P_{\rm absorb}=1.
```

反射光返回水中继续传播，从而自然形成晚光、多路径和不同 PMT 间的相关性。可采用
带权 Russian roulette 控制罕见长反射链，但必须报告被截断或 roulette 终止的
比例。

现有手工径向响应多项式只能用于迁移对照。正式数据中必须删除，使
`g(r, theta, phi)` 由边界、离散 PMT、固定结构和 PMT 光学自然产生，避免重复计数。

## 6. 第三闭环：PMT、电子学与 ADC

### 6.1 固定私有 detector realization

每根 PMT 的固定私有参数为

```math
\Theta_i=\{PDE_i,gain_i,TTS_i,\Delta t_i,SPE_i,DCR_i,\ldots\}.
```

参数在整个 release 内固定，不能逐事件重抽。两种 PMT 分别从公开测量约束的分布族
抽样，并允许 PDE、gain、TTS、SPE 宽度和 DCR 存在有依据的弱相关或批次相关。
随机种子固定，具体 realization 不公开。

### 6.2 光电子和 PMT 电流

每个被探测光子从类型相关 SPE 分布抽取电荷。最小模型包含主分量、低电荷尾和小
比例大电荷尾。光电子时间为 photon arrival、固定通道 time offset 和非高斯 TTS
之和；TTS 包含主峰及小比例 late-pulse，prepulse 只有在公开依据和影响评估支持时
才加入。

两类 PMT 使用不同的单 PE 脉冲模板：

```math
I_i(t)=\sum_kq_kh_{\mathrm{type}(i)}(t-t_k).
```

模板包含 rise/fall、脉冲宽度和有依据的小幅 overshoot/ringing。多 PE 靠近时自然
重叠。afterpulse 保持真实的微秒时间尺度；若读出窗不覆盖它，不得把它人为提前来
增加难度。

### 6.3 电子学和数字化

模拟电压为

```math
V_i(t)=\mathcal H_i[I_i(t)]+b_i(t)+n_i(t),
```

包括类型相关前端传递函数、固定通道增益、事件基线、低频漂移、高频白噪声、小幅
公共噪声、ADC 动态范围和量化。基线必须能从 pre-trigger 样本估计。任何不可观测
的随机漂移都会制造无意义的信息损失，禁止加入。

饱和和双增益切换只在未饱和预生产样本确实覆盖硬件动态范围时启用，并报告触发
比例；不能为了增加难度强行压缩波形。

### 6.4 JUNO 对齐的 trigger/readout 语义

JUNO 的 FADC 连续数字化波形，ADC 本身没有“低于幅度阈值就不采样”的步骤。阈值
位于后续层：FPGA local hit、global trigger、在线数据保留和波形重建。设计顺序为

```math
\text{analog waveform}
\rightarrow\text{continuous FADC}
\rightarrow\text{digital waveform}
\rightarrow\text{local hit}
\rightarrow\text{global trigger}
\rightarrow\text{readout window}
\rightarrow\text{storage policy}.
```

公开资料显示 JUNO LPMT 使用 14-bit、1 GS/s 双增益数字化；local hit 默认以数字
波形越过通道基线噪声的 `5 sigma` 形成，全局 multiplicity trigger 后抽取约
1008 ns 窗口。阈值和窗口作为 release 配置公开并冻结。OEC 可按事件
类型保存全部完整波形、仅 fired PMT 完整波形或 charge--time 数据。JunoResBench
采用 **fired-PMT full-window** 模式：

- 公开每个事件的完整 channel map；
- fired PMT 保存完整 raw ADC 窗口；
- 未保存通道显式标为 `not_fired`；
- 不在波形内部裁剪 pulse ROI；
- 不预做 baseline subtraction、gain/time correction 或 COTI charge；
- 允许文件层无损压缩，解码后必须恢复原始 ADC counts。

这既保留波形算法自由度，也控制全体积纯基线波形造成的磁盘负担。

相关公开依据包括：

- JUNO electronics readout system：<https://arxiv.org/abs/2110.12277>
- JUNO simulation software：<https://arxiv.org/abs/2212.10741>
- JUNO initial performance：<https://hepnp.ihep.ac.cn/en/article/id/aff4b441-8f4e-4a9f-a7ca-fc5e9e2eac88>
- Real-Time Wiener Deconvolution：<https://arxiv.org/abs/2603.25436>
- Prediction of energy resolution：<https://cpc.ihep.ac.cn/article/doi/10.1088/1674-1137/ad83aa>

## 7. 公开刻度数据与隐藏 truth

训练数据不直接提供逐事件 `E_true, x_true, y_true, z_true`。公开数据分为三类。

### 7.1 通道标定

- laser injection 的标称时间和相对光强档位；
- periodic/random-trigger 空波形；
- PMT ID、公开位置和 PMT 类型；
- 不提供真实 PDE、gain、TTS、time offset 或逐 PE truth。

agent 可据此估计基线、SPE、增益、时间偏移和噪声。

### 7.2 物理刻度源

每个 run 只公开 source 类型、标称能量或衰变谱、标称部署位置及定位不确定度。
不公开逐事件沉积能量、沉积重心、径迹、光子或 PE truth。单电子档采用离散理想化
电子刻度扫描；多 gamma 档采用真实可部署源语义。

### 7.3 盲测物理数据

测试波形可见，但逐事件 `E_true` 和 `x_true,y_true,z_true` 仅由 evaluator 私有读取。
单电子测试集连续覆盖 1--10 MeV 和体积位置；多 gamma/IBD-like 测试集包含不同的
多点拓扑。训练和测试共享同一 detector realization，不允许测试时突然更换一套
完全未知的通道响应。

可另发极小的、truth 完全公开的 toy dataset 检查 I/O；它不参与性能评价。

## 8. 白盒 evaluator 与三方隔离

“白盒 evaluator”指评分源码、公式、分桶和错误处理公开，不表示测试 truth 公开。
目录和依赖边界为：

```text
JunoResBench/
  generator/   # 产生波形和私有 truth，不进入 agent world
  dataset/     # 公开波形、几何、类型和 calibration metadata
  evaluator/   # 公开评分代码，运行时在 world 外读取隐藏 truth
```

三者互不 import、互不调用。generator 不能借用 evaluator；evaluator 不能调用
generator；dataset 只是静态发行物，不携带可执行产生逻辑。

正式 evaluator：

- 不在代码中嵌入 truth 或私有 detector 参数；
- 只接受完整测试集预测，不接受任选事件子集；
- 不返回逐事件 residual；
- 只返回预注册的全局和粗分桶统计；
- 在线反馈集与最终 holdout 集分离，最终 holdout 从未参与优化反馈。

## 9. 任务输出和四指标验收包络

每个事件必须输出

```text
event_id, E_rec, x_rec, y_rec, z_rec
```

不把四项指标线性加权成单一分数，以免用位置换能量或用 bias 换方差。正式结果是

```math
(R_E,\ |B_E|,\ R_x,\ |B_x|).
```

### 9.1 能量分辨率

在隐藏单能点计算 `sigma(E_rec-E_true)/E_true`，再拟合

```math
\frac{\sigma_E}{E}=
\sqrt{\frac{a^2}{E}+b^2+\frac{c^2}{E^2}}.
```

headline target 固定为

```math
R_E(1\ \mathrm{MeV})\leq3.0\%.
```

常数预测可在纯 1 MeV 样本上伪造零方差，因此有效提交还必须在隐藏的 1--10 MeV
样本上满足能量响应单调和预注册的平均响应合理性检查。这是指标有效性条件，不是
新的优化目标。

### 9.2 能量 bias

```math
B_E(E,\mathbf{x})=
\frac{\mathbb E[E_{\rm rec}-E_{\rm true}]}{E_{\rm true}}.
```

报告全局值以及按能量、半径、角区、遮挡区和边缘区的 bias。验收使用有足够统计量
分桶中的最大绝对 bias，避免正负区域互相抵消。

### 9.3 位置分辨率

令 `Delta x = x_rec-x_true`，主指标为三维 68% containment：

```math
R_x(E)=Q_{68}(\|\Delta\mathbf{x}\|).
```

同时报告 Cartesian、radial、tangential 分辨率及其对能量和位置的依赖。

### 9.4 位置 bias

除分区 Cartesian bias 外，重点计算

```math
B_r(E,\mathbf{x})=
\mathbb E[\Delta\mathbf{x}\cdot\hat{\mathbf r}],
```

防止球对称样本中全局向量均值掩盖系统性的 inward/outward bias。

### 9.5 目标冻结原则

- 位置分辨率目标取私有 oracle 极限的 1.10--1.15 倍；
- 能量 bias 上限取 3.0% 能量分辨率目标的十分之一，即 0.3%；
- 位置 bias 上限取位置分辨率目标的十分之一；
- 四项均以预注册 bootstrap 置信区间判定；
- 所有阈值在正式 test production 前冻结，不根据 agent 结果回调。

总体通过要求四项都进入目标包络。3.0% 是最醒目的长期攻关锚点，但任务不是单指标
任务；位置分辨率、能量 bias 和位置 bias 同样是正式验收量。

## 10. 可达性与非平凡性 gate

### 10.1 可达性下界

用不公开的中间 truth 构造出题方 oracle：已知真实位置、逐管响应、真实 PE 数与
时间，不受 ADC 反演误差影响。oracle 的 1 MeV 等效能量分辨率应有余量地优于 3%，
建议处于 2.6%--2.8%。若 oracle 超过 3%，题目先验不可达，禁止发行。

位置 oracle 使用与 evaluator 相同的 68% containment 定义，决定位置验收锚点。
oracle 只验证信息是否存在，不进入 agent 研究包。

### 10.2 简单统计量上界

正式生产前依次检查：

1. 总 ADC 积分的全局线性映射；
2. 总电荷加一维径向修正；
3. 总电荷加真实半径的平滑修正；
4. hit 数加总电荷；
5. 少量低阶球谐电荷矩。

简单模型 1 MeV 分辨率的单侧置信下界必须高于 3.0%，且换种子后结论稳定。失败应
来自可观察的角向、通道和波形残余结构，而不是提高白噪声、降低 PE 产额或随机删除
数据。

### 10.3 信息增量探针

固定训练数据和模型容量，比较

```math
Q_{\rm total}
\rightarrow\{Q_i\}
\rightarrow\{Q_i,t_i^{\rm first}\}
\rightarrow\{\mathrm{ADC}_i[n]\}.
```

不要求该廉价探针本身达到 3%，但加入逐 PMT 电荷、时间和完整波形后应在相应物理
区域出现稳定改善。若某层没有增量信息，回查其物理闭环，不能仅在题目说明中宣称
该层重要。

这套 gate 不规定 agent 必须使用 QMLE、TMLE 或 QTMLE，只排除总电荷成为近似充分
统计量的失败题目。

## 11. 发行 QA 报告

每个 release 必须由独立 QA 工具生成可直接阅读的报告和图片。QA 属于出题基础设施，
读取专门的私有审计样本，不进入 agent 研究包，也不被 evaluator 调用。

报告至少包含：

- stopping-power、逐 step quenching 和可见能量；
- scintillation/Cherenkov 光谱、时间和产额；
- 光子路径长度、吸收、散射、再发射、反射和终止原因；
- Snell/Fresnel/TIR 数值闭合；
- 中心与边缘 hit pattern、`r/theta/phi` 电荷响应图；
- prompt/late charge map 和传播时间分布；
- 两类 PMT 的 PDE、SPE、TTS、time offset 和平均波形；
- baseline、噪声 PSD、ADC 动态范围和饱和率；
- trigger efficiency、fired-PMT 数和 waveform occupancy；
- truth photon time、PE time 和 ADC pulse time 三层对照；
- 1--10 MeV response、能量 bias、位置 bias 和分辨率；
- 完整消融链的分辨率预算；
- oracle 可达性、简单模型和信息增量探针结果；
- 生产配置、代码 commit、seed、输入表 checksum 和事件计数。

关键硬 gate 包括：概率守恒、光子账目闭合、中心旋转对称、固定结构角向响应、随机
种子复现、trigger/readout 账目闭合、截断率和饱和率在预注册范围内。

## 12. 计算策略与非目标

生产运行在 IHEP HTCondor 集群完成，本地只运行小样本测试和 QA。逐光子传播使用
NumPy 批处理、按介质压缩活跃光子和解析几何求交。性能优化不得改变权威分布。

当前阶段明确不做：

- 完整 Geant4/JUNOSW 依赖；
- 完整 detector CAD 光线追踪；
- 直接复制 JUNOSW 逐 PMT 性能参数；
- 为了困难任意增大 DCR、white noise、afterpulse 或响应散布；
- 逐事件不可观测 detector 随机化；
- 预先限制 agent 使用总电荷、QMLE、TMLE、QTMLE 或神经网络；
- 将 generator、dataset、evaluator 通过共享代码重新耦合。

## 13. 实施完成定义

只有以下条件同时满足，才允许生产和发布新的单电子题库：

1. 三个物理闭环实现并通过单过程统计测试；
2. calibration、blind test 和 white-box evaluator 接口完成；
3. generator/dataset/evaluator 独立性测试通过；
4. fired-PMT full-window 数据可无损解码；
5. 四指标 evaluator 及防常数预测检查通过；
6. oracle 优于目标，简单统计量不能达到目标；
7. 信息增量探针检出逐 PMT、时间或波形中的额外信息；
8. 完整 QA 图册和 provenance manifest 生成；
9. 集群小规模 smoke production 通过后，再提交正式 HTCondor production。

完成单电子档验收后，同一探测器链扩展到多 gamma/IBD-like positron 档，不另造一套
简化响应世界。
