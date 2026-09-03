# JunoResBench：\(P(\mathrm{ADC\ waveforms}\mid E,\mathbf{x})\) 依赖关系调研

## 1. 报告目的与边界

JunoResBench 的第一轮单电子测试表明，总波形积分电荷经过简单标定后就能很好地
恢复能量。这不是参赛 agent 使用了不合理捷径，而是隐藏世界把大部分能量信息压缩
成了近似一维、近似线性的统计量。为判断产生子还缺少哪些真实结构，本报告从
JUNO 尺度液闪探测器的完整因果链出发，研究

```math
P\!\left(\{\mathrm{ADC}_i[n]\}\mid E,\mathbf{x}\right)
```

由哪些物理过程、探测器潜变量和电子学过程共同决定。

本报告是 **benchmark 设计调研**，不是开发 TODO，也不是题库材料。它位于 bench
项目的 `docs/` 中，不进入公开 dataset、任务说明或 agent 研究包。表中的“当前
产生子”只描述报告编写时 `main` 源码的事实状态；已经生成的外部 release 可能来自
更早的提交，不能用该列替代 release 级验证。

研究原则是：实现可以分批，但物理依赖图不能因为某项难做而删掉。某项效应是否
应进入最终替代世界，必须根据它对条件均值、条件方差、观测相关性和信息损失的
实际影响判断。

## 2. 从确定函数改写为条件分布

将探测器写成

```math
Q=f(E_{\rm true})
```

会隐藏问题的主要结构。更完整的生成关系是

```math
(E,p,\mathbf{x},\mathbf{d})
\rightarrow
\{\Delta E_k,\mathbf{x}_k,t_k,(dE/dx)_k\}
\rightarrow
\{\gamma_j:\lambda_j,\mathbf{n}_j,t_j,\mathbf{x}_j\}
\rightarrow
\{\mathrm{PE}_{ij},q_{ij},t_{ij}\}
\rightarrow
\{V_i(t)\}
\rightarrow
\{\mathrm{ADC}_i[n]\}.
```

若以 \(\mathbf Z\) 代表径迹、光子产生、传播历史、PMT 响应和电子学状态等隐变量，
真正的研究对象是

```math
P(\mathbf W\mid E,\mathbf{x})
=
\int P(\mathbf W,\mathbf Z\mid E,\mathbf{x})\,d\mathbf Z,
\qquad
\mathbf W=\{\mathrm{ADC}_i[n]\}.
```

固定的逐通道标定参数也不能从图中删去。更严格的形式是

```math
P(\mathbf W\mid E,\mathbf{x},\mathcal C,\mathcal S),
```

其中 \(\mathcal C\) 是 PDE、gain、TT、TTS、SPE 谱和角响应等探测器标定，
\(\mathcal S\) 是温度、磁场、失效通道、基线和时间漂移等运行状态。最终的
\(P(\mathbf W\mid E,\mathbf{x})\) 对未知或变化的 \(\mathcal C,\mathcal S\)
继续边缘化。

下表使用四类影响标记：

- **M**：改变条件均值，产生能量非线性或空间非均匀性；
- **V**：改变条件方差，影响能量或顶点分辨率；
- **C**：产生电荷、时间、位置、波长或通道之间的相关性；
- **L**：通过阈值、饱和、窗口、dead time 等造成不可逆信息损失。

“重要性”针对当前 1--10 MeV LPMT 波形重建问题，而不是对所有 JUNO 物理分析的
普适排序。

## 3. 粒子输运与可见能量

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 初级粒子类型 | 不同的相互作用、次级粒子谱和发光响应 | M/V/C | 电子档固定；跨档位极高 | 已区分 \(e^-\)、\(e^+\)、\(\gamma\) |
| 初始方向 | 决定短径迹方向和 Cherenkov 锥轴 | C | 中等，边界附近增强 | 已产生并传播 |
| 逐步能损 \(dE/dx\) | 每个局部沉积独立进入 Birks 积分 | M/V | 高 | 已按 stopping-power 步进 |
| 能损涨落与 range straggling | 同一 \(E\) 产生不同局域沉积序列 | V/C | 中等 | 基本没有，接近确定 CSDA |
| delta ray 和次级电子 | 把能量分散到不同方向和位置 | V/C | 中等，随能量上升 | 未显式实现 |
| 制动辐射 | 电子能量转为可传播 gamma | V/C/L | 低能较弱，接近 10 MeV 时增强 | 未实现 |
| gamma Compton/光电链 | 多点沉积、低能次级电子和边界逃逸 | M/V/C/L | IBD 档极高 | 已有参数化链 |
| 生产阈值与 cut | 决定次级粒子是独立追踪还是局域沉积 | M/V | 中等 | 使用合成 transport cut |
| 局部 quenching | \(\sum_k\Delta E_k/[1+k_B(dE/dx)_k]\) | M/V/C | 高 | 已逐步计算 |
| 事例内 quenching 涨落 | 局部能损涨落令光产额偏离简单 Poisson | V/C | 高 | 未独立建模 |

JUNO 的公开 full-MC 研究逐 step 记录能损并执行 Birks 积分，而且指出 production
cut 会改变次级粒子和有效 \(k_B\) 的解释。更重要的是，总 PE 宽度显著大于纯
Poisson：分辨率分解中包含 scintillation Poisson、quenching、Cherenkov 以及
scintillation--Cherenkov covariance。因此“\(E_{\rm vis}\to N_\gamma\) 主要是
Poisson”只能作为起始近似，不能作为完整依赖结构。

## 4. 光子产生

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| scintillation 绝对光产额 | \(\bar N_s=Y_sE_{\rm vis}\) | M/V | 极高 | 已实现 |
| scintillation 计数涨落 | \(P(N_s\mid E_{\rm vis},dE/dx)\) | V | 极高 | Poisson |
| 多分量发光时间 | \(P(t_{\rm emit}\mid p,dE/dx)\) | C | 高 | 统一四指数 e-like 谱 |
| 时间谱的粒子/电离密度依赖 | 不同粒子和 \(dE/dx\) 改变慢光比例 | M/C | 单电子中低；跨粒子高 | 未实现 |
| scintillation 发射光谱 | 决定吸收、再发射、群速度和 QE | M/V/C | 高 | 有合成波长谱 |
| Cherenkov 产额 | 依赖 \(\beta\)、路径长度和 \(n(\lambda)\) | M/V/C | 中等但信息价值高 | 简化 Frank--Tamm |
| Cherenkov 原始光谱 | 近似 \(1/\lambda^2\)，且阈值依赖色散 | M/C | 高 | 未与 scintillation 分开 |
| Cherenkov 方向性 | 锥角依赖 \(\beta,n(\lambda)\) | C | 中等 | 有锥方向，折射率为常数 |
| 两类光的协方差 | 共享能损径迹和总能量约束 | V/C | 高 | 未完整保留 |

真实 JUNO 模拟按波长相关折射率计算 Cherenkov 阈值、产额和光子能量，并用实验
非线性和能标约束短波不确定性。scintillation 与 Cherenkov 不应先被混成同一种
光子再输运；它们不同的光谱、方向和时间正是联合重建中的潜在信息。

## 5. 液闪内部光学传播

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| \(n_{\rm LS}(\lambda)\) | 改变折射、Cherenkov 阈值和传播速度 | M/C | 高 | 常数 1.49 |
| 色散与群速度 | 不同波长产生不同 TOF | C | 高 | 未实现 |
| 吸收长度 \(L_{\rm abs}(\lambda)\) | 生存概率依赖路径和波长 | M/V/C | 极高 | 已实现 |
| Rayleigh 长度 \(L_R(\lambda)\) | 改变方向和路径长度 | M/V/C | 高 | 已实现 |
| Rayleigh 角分布 | 决定散射后的空间与时间 pattern | C | 中高 | 参数化实现 |
| 再发射量子产额 | 决定光子死亡或继续传播 | M/V | 高 | 已实现 |
| 再发射红移 | 改变后续吸收、QE和TOF | M/C | 高 | 已实现 |
| 再发射延迟 | 产生与传播历史相关的晚光 | C | 高 | 已实现 |
| 多次吸收/再发射 | 形成长路径和非高斯晚光尾 | M/V/C | 高 | 最多20代 |
| LS 光学空间非均匀 | 光学参数随位置、批次和纯化状态改变 | M/V/C | 真实运行中可能重要 | 未实现 |

JUNO 将 LAB、PPO 和 bis-MSB 等效为一套联合光学模型，同时处理发射谱、吸收、
散射、再发射量子产额、红移和延迟。这里最重要的不是过程名称数量，而是同一个
光子的波长、传播距离、到达时间和最终可探测概率必须沿一条因果历史共同演化。

## 6. 多介质边界和宏观几何

这一层是当前产生子与 JUNO full MC 差异最大、也最可能制造非光滑边缘响应的
部分。

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 17.7 m LS 球边界 | 决定离开 LS 的位置和角度 | M/C | 极高 | 用单一传播球近似 |
| 12.4 cm acrylic | 折射、吸收和额外路径 | M/V/C | 高 | 未显式存在 |
| 约1.8 m水缓冲层 | 折射、吸收、散射和额外TOF | M/V/C | 高 | 未显式存在 |
| LS--acrylic Fresnel | 透射/反射依赖波长和入射角 | M/C | 极高 | 未实现 |
| acrylic--water Fresnel | 第二次折射和反射 | M/C | 高 | 未实现 |
| water--PMT glass 边界 | 改变PMT入射角和反射概率 | M/C | 高 | 未实现 |
| 全反射 TIR | 产生边缘突变、多路径和局域盲区 | M/V/C | 极高 | 未实现 |
| acrylic/water 吸收与散射 | 增加路径相关损失和延迟 | M/V/C | 中高 | 未实现 |
| PMT间隙与离散覆盖 | 对球面光场作离散空间采样 | M/V/C | 高 | 已用真实LPMT位置和圆盘 |
| PMT前后保护罩 | 遮挡、折射和反射 | M/C | 中高 | 未实现 |
| 590个acrylic连接节点 | 产生方向相关遮挡 | M/C | 中高 | 未实现 |
| 钢结构和光学mask | 遮挡、反射并破坏球对称性 | M/C | 高 | 统一ESR边界替代 |
| chimney和顶部结构 | 产生局部 \((\theta,\phi)\) 非均匀 | M/C | 局部很高 | 未实现 |
| LPMT/SPMT混排 | 改变间隙覆盖并提供独立计数系统 | M/C | 对真实JUNO高 | 当前只有LPMT |
| 表面粗糙度 | 改变镜面/漫反射比例 | M/C | 次级但真实 | 未实现 |

JUNO full MC 显式包含 LS、acrylic、水、PMT 玻璃的波长相关折射率，以 Fresnel
过程处理多个界面，并包含连接节点、钢结构、保护罩和光学隔离结构。对重建而言，
全反射和结构遮挡的价值不只是降低光量，而是使

```math
P(i,t_{\rm arrive}\mid\mathbf{x},\lambda)
```

在边界附近呈现非光滑、多路径和强方位依赖，无法被低阶 \(f(r)\) 完整吸收。

## 7. PMT 光学探测

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 逐管 PDE | 每个通道具有不同检测概率 | M/V/C | 高 | 合成逐管总体 |
| PMT 类型 | HPK、NNVT、HQE属于不同响应族 | M/V/C | 高 | 已区分部分参数 |
| QE 波长响应 | \(QE_i(\lambda)\) | M/C | 高 | 统一相对QE曲线 |
| CE 入射角响应 | \(CE_i(\theta)\) | M/C | 极高，边缘增强 | 单一NNVT曲线 |
| 光阴极落点 | PDE、TT、TTS、SPE谱随表面位置变化 | M/V/C | 高 | 未实现 |
| 方位角非均匀 | \(PDE(\theta,\phi)\) | M/C | 中高 | 未实现 |
| 光阴极多层干涉 | 反射和吸收依赖 \(\lambda,\theta\) | M/C | 高 | 未实现 |
| PMT反射光回流 | 未探测光子可反射并击中其他PMT | M/V/C | 高 | 命中后终止 |
| PMT内部反射/电极遮挡 | 改变吸收和有效面积 | M/C | 中高 | 未实现 |
| 磁场与PMT朝向 | 改变PDE、TT和TTS | M/V/C | 中等 | 未实现 |
| photon-to-PE抽样 | 每个入射光子作Bernoulli探测 | V | 极高 | 已实现 |

JUNO 的 PDE 是 QE、CE 和有效面积的组合。公开量产测试显示 HPK 与 NNVT 在 PDE、
TTS、SPE谱和光阴极均匀性上都不同。JUNO PMT 光学模型进一步把水、玻璃、减反膜、
光阴极和真空视作多层结构，计算波长和入射角相关的吸收、干涉和反射。

当前源码存在一个需要在研究结论中明确记录的因果断点：光学 trace 已经更新光子
方向并计算最终入射角，但 Stage 4 对 scintillation 重新使用沉积点到PMT的弦方向，
对 Cherenkov 使用初始发射方向。发生过散射或反射的光子因而失去

```math
\text{传播历史}\leftrightarrow
\text{最终入射角}\leftrightarrow
\text{CE/PDE}\leftrightarrow
\text{到达时间}
```

这一物理相关性。

## 8. PMT 电荷与时间响应

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 逐管 TT offset | 固定通道时间偏置 | C | 高，取决于标定精度 | 合成实现 |
| TTS | 单PE transit time随机展宽 | V/C | 高 | 已按类型实现 |
| TT/TTS落点依赖 | 光阴极不同位置具有不同时间响应 | V/C | 高 | 未实现 |
| NNVT多峰TT | 非高斯时间PDF和卫星峰 | V/C | 高 | 合成对称卫星峰 |
| SPE电荷分辨率 | 单PE放大增益随机 | V | 极高 | 已实现 |
| NNVT大电荷长尾 | 电荷PDF具有非高斯尾 | M/V | 高 | log-normal近似 |
| SPE谱落点依赖 | 光阴极赤道和中心响应不同 | M/V/C | 中高 | 未实现 |
| 逐管gain | 通道电荷尺度不同 | M/V/C | 高 | 合成实现 |
| PMT电荷非线性 | 多PE响应不再严格可加 | M/L | 近端高占用通道可重要 | 未实现 |
| DCR | 读出窗内混入随机PE | M/V/C | 1 MeV极高 | 已逐管实现 |
| pre-pulse | 形成提前时间尾 | V/C | 较低但影响首光 | 未实现 |
| late pulse | 形成延迟非高斯尾 | V/C | 中等 | 部分混入TTS近似 |
| afterpulse | 与主脉冲相关的微秒延迟脉冲 | M/V/C | 对JUNO IBD能量分辨率较小 | 当前模型偏早、偏强 |
| dead/hot channel | 通道缺失或异常噪声 | M/V/C/L | 中等 | 未实现 |
| gain/PDE时间漂移 | 运行状态与标定状态失配 | M/V | 真实数据中重要 | 冻结世界未实现 |

当前 `afterpulse_tau=500 ns` 且读出窗为 1 μs，使一部分 afterpulse 明显进入主事件
波形；JUNO 公开分辨率研究认为最早主要 afterpulse 约在 1 μs，和主信号重叠较小，
对 IBD 能量分辨率影响可忽略。这说明“加入一个真实效应的名称”仍可能得到不真实
的难度，必须同时验证其时间尺度和观测后果。

## 9. 模拟电子学与 ADC

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 类型相关SPE模板 | 不同rise/fall/FWHM | C | 高 | 统一模板 |
| pulse卷积 | 多个PE叠加为连续波形 | C | 极高 | 已实现 |
| pulse overlap | 相邻PE不再可直接分辨 | V/C/L | 极高 | 已存在，模板较简单 |
| overshoot | 改变基线恢复和积分电荷 | M/C | 高 | 未实现 |
| cable reflection/ringing | 产生延迟相关伪脉冲 | M/C | 中高 | 未实现 |
| analog bandwidth/shaping | 抑制高频并造成时域展宽 | C/L | 高 | 由单一模板隐式近似 |
| 高低增益双链路 | 动态范围分段和交接 | M/C | 中高 | 未实现 |
| 放大器饱和 | 高局域占用时压缩响应 | M/L | 近端PMT可能重要 | 未实现 |
| PMT/电子学非线性 | 输出charge不再严格可加 | M/L | 高于单纯量化 | 未实现 |
| 白噪声 | 独立样本噪声 | V | 中等 | 已实现 |
| 有色/相关噪声 | 改变matched filter和baseline估计 | V/C | 高 | 未实现 |
| pedestal/baseline漂移 | 产生通道和事例相关积分偏差 | M/V/C | 高 | 固定baseline |
| sampling phase | pulse相对1 ns网格随机 | V/C | 中等 | 连续pulse时间已保留部分效果 |
| clock jitter | 采样时间不确定 | V/C | 较低 | 未实现 |
| ADC量化 | 有限bit数 | V/L | 较低 | 14 bit |
| ADC DNL/INL | code response非线性 | M | 中等 | 未实现 |
| ADC clipping | 超量程信号被截断 | M/L | 高占用时重要 | 有数值clip，缺少真实双增益逻辑 |

JUNO LPMT 链路使用高、低增益两路放大和 1 GHz、14 bit FADC。公开 electronics
simulation 包含 HPK/NNVT 类型相关平均 SPE 模板、overshoot、白噪声、增益链、
baseline、digitization 和非线性。公开 waveform 研究还显示，SPE charge smear 和
pulse overlap 会让简单积分不能精确恢复 PE 数，基于 photon counting 的波形重建
可以改善能量分辨率。

## 10. Trigger、窗口、零抑制与数据获取

| 依赖因素 | 进入条件分布的机制 | 影响 | 重要性 | 当前产生子状态 |
|---|---|---:|---:|---|
| 全局trigger阈值 | trigger time依赖总光到达过程 | M/V/C/L | 高 | 已实现 |
| trigger time walk | 低能、慢光和边缘事例更晚触发 | M/C/L | 高 | 部分自然产生 |
| 有限读出窗 | 丢失晚光、暗噪声和延迟脉冲 | M/V/C/L | 极高 | 1 μs窗口 |
| 电荷积分窗 | 收集比例依赖位置和传播路径 | M/C/L | 极高 | 由agent从波形选择 |
| zero suppression | 低幅SPE被选择性丢失 | M/V/L | 极高 | sparse ROI |
| ROI扩展与合并 | 改变可见baseline和pulse上下文 | M/C/L | 高 | 已实现 |
| channel dead time | 多pulse或连续触发时信息缺失 | M/L | 中等 | 未实现 |
| event pile-up | 无关事例与目标信号叠加 | M/V/C | 低能和长窗口中重要 | 未实现 |
| 放射性偶然本底 | 非均匀、非纯Poisson污染 | M/V/C | 低能重要 | 只有DCR |
| 保存策略 | full waveform或只存charge/time | L | 对benchmark定义极高 | 固定为稀疏波形 |

阈值和窗口能够制造真正的不可逆非线性。例如低幅SPE经过阈值后，通常有

```math
E[Q_{\rm kept}\mid N_{\rm PE}]\ne cN_{\rm PE},
```

而丢失率同时依赖 pulse overlap、PMT类型、baseline和到达时间分布。因此电子学
难度不能只靠增加白噪声来实现。

## 11. 固定标定、运行状态与可辨识性

固定的逐PMT差异会增加研究工作量，但未必长期构成困难。如果产生子在所有事件中
使用同一套 PDE、gain 和 time offset，公开标定又足够密集，agent 可以把它们估成
一张响应表。此时通道异质性仍然真实，却可能被一次性消除。

必须分别研究三类量：

| 类型 | 例子 | 对问题的意义 |
|---|---|---|
| event-wise随机量 | 光子数、传播历史、SPE gain、DCR | 形成不可约或可估计的统计涨落 |
| detector-fixed潜变量 | 逐管PDE、gain、TT、TTS、表面响应 | 需要从有限标定中学习 |
| run-wise状态量 | 温度、baseline、失效通道、gain漂移 | 造成标定与物理样本之间的domain shift |

如果替代世界永远冻结、标定无限密集且响应可以低维分解，复杂的逐管常数最终仍会
退化为查表问题。真正的研究深度来自有限标定条件下的高维插值、charge--time
相关性、位置--能量耦合以及DAQ造成的信息损失。

## 12. 哪些结构分别支持 QMLE、TMLE 与联合模型

### 12.1 QMLE 的物理来源

总电荷只有在所有通道可交换、响应线性且无通道选择损失时才接近充分统计量。若

```math
P(Q_i\mid \mu_i,E,\mathbf{x},\mathrm{type}_i)
```

随PMT类型、入射角、占用数、SPE谱和阈值而改变，逐PMT charge likelihood 才比
\(\sum_iQ_i\) 保留更多信息。

### 12.2 TMLE 的物理来源

时间模型的价值来自一个复杂但可标定的

```math
P(t_i\mid\mathbf{x},\lambda,
\text{optical history},\mu_i,\mathrm{type}_i),
```

其中 prompt 光、散射、再发射、折射、TIR、多路径、TT/TTS 和dark noise形成非高斯
残余时间PDF。若只保留直线TOF加高斯TTS，时间通常只提供粗略位置，不足以形成长期
研究问题。

### 12.3 QTMLE/联合模型的物理来源

联合模型有额外信息的条件不是“同时保存了charge和time”，而是近似因子化失效：

```math
P(Q,T\mid E,\mathbf{x})
\ne
P(Q\mid E,\mathbf{x})P(T\mid\mathbf{x}).
```

典型耦合包括：

- 传播路径同时决定到达时间和吸收概率；
- 入射角同时决定 PDE、CE、TT、TTS和SPE谱；
- pulse overlap同时改变可恢复charge和first-hit time；
- trigger/window根据时间选择保留下来的charge；
- 顶点误差通过空间非均匀响应传播到能量误差；
- 边界多路径使相同总电荷对应不同时间结构和位置假设。

JUNO 的公开重建工作使用标定数据建立逐PMT expected-nPE map和残余时间PDF，联合
重建能量与顶点；公开结果指出charge与time结合尤其改善acrylic球边缘，并自然处理
能量和顶点的强相关。

这些算法名称不是给参赛agent规定的路线。它们是诊断产生子是否真的包含多层可用
信息的物理探针；任何算法只要能从原始波形提取同等信息都应被允许。

## 13. 对当前产生子结构的研究判断

当前产生子已经不是“没有光学”的简单玩具：它包含逐步沉积、局部 Birks、两类
发光、逐光子吸收/再发射/Rayleigh/ESR、真实LPMT位置、逐管合成响应、DCR、TTS、
SPE charge、trigger、ADC和稀疏波形。

但效应数量不能替代因果闭合。当前最重要的结构性判断是：

1. trace 后仍乘手工径向多项式，把部分光学非均匀性重新压缩成易拟合的
   \(f(r)\)，并可能与显式传播重复；
2. 最终传播方向没有传给PMT角响应，切断了传播时间、入射角和探测概率的相关；
3. scintillation与Cherenkov共用发射光谱，削弱prompt光独特的波长--时间结构；
4. 单介质球面加统一ESR不能产生LS--acrylic--water的折射、Fresnel和TIR边界结构；
5. PMT命中后即终止，缺少PMT光学反射和光子回流；
6. 固定逐管差异可能被密集标定退化为查表；
7. 统一线性pulse模板加白噪声仍使波形积分接近总PE的代理；
8. 当前afterpulse时间尺度可能制造了偏离JUNO IBD情形的伪难度。

因此，上一版失败不能归结为“噪声不够大”。更准确的结论是：已有物理过程尚未
共同形成足够丰富且因果闭合的

```math
P(\mathrm{ADC\ waveforms}\mid E,\mathbf{x}).
```

前半段决定产生多少、什么类型、什么时空结构的光；光学、PMT和电子学把这些光映射
为高维、非交换、有损并带相关性的观测。二者必须共同建模。单纯增加独立噪声只会
恶化可达分辨率，不会自然产生 QMLE、TMLE 或联合模型的研究价值。

## 14. JUNO公开结果提供的量级锚点

JUNO 2025年公开的full detector、electronics、calibration和reconstruction全链研究
给出了一个有用的事实校验：默认情形的拟合分辨率约为2.95% at 1 MeV。依次去除
vertex uncertainty、dark noise、waveform reconstruction uncertainty和SPE charge
smear，分辨率单调改善。论文对约1.022 MeV点给出的quadrature分解为：理想部分
2.90%，顶点0.35%，dark noise 0.83%，waveform reconstruction 0.39%，SPE smear
0.58%，合计3.12%。

这些数值不能直接复制为JunoResBench参数，因为本benchmark的中心PE yield、题目
人口和目标定义不同；但它们否定了两个极端判断：

- 后半段并非只改变均值，DCR、SPE和waveform reconstruction对分辨率有实质量级；
- 后半段也不是越复杂越好，真实JUNO中vertex和waveform项的贡献有明确量级，错误
  时间尺度或过强噪声会制造不受物理支持的困难。

## 15. 资料来源与证据边界

本报告优先使用JUNO Collaboration或相关作者公开的论文，不直接读取JUNOSW逐管
性能数据库作为替代世界参数。

1. JUNO Collaboration, *Prediction of energy resolution in the JUNO
   experiment*, Chinese Physics C 49, 013003 (2025).
   <https://cpc.ihep.ac.cn/article/doi/10.1088/1674-1137/ad83aa>
2. JUNO Collaboration, *Simulation software of the JUNO experiment*,
   European Physical Journal C 83, 382 (2023).
   <https://link.springer.com/article/10.1140/epjc/s10052-023-11514-x>
3. JUNO Collaboration, *Mass testing and characterization of 20-inch PMTs
   for JUNO*, European Physical Journal C 82, 1168 (2022).
   <https://link.springer.com/article/10.1140/epjc/s10052-022-11002-8>
4. W. Luo et al., *Machine-learning based photon counting for PMT waveforms
   and its application to the improvement of the energy resolution in large
   liquid scintillator detectors*, European Physical Journal C 84, 1235
   (2024). <https://link.springer.com/article/10.1140/epjc/s10052-024-13724-3>
5. G. Huang et al., *Data-driven simultaneous vertex and energy
   reconstruction for large liquid scintillator detectors* (2022).
   <https://arxiv.org/abs/2211.16768>
6. JUNO Collaboration, *JUNO Physics and Detector*, Progress in Particle and
   Nuclear Physics 123, 103927 (2022).
   <https://arxiv.org/abs/2104.02565>
7. Y. Ren et al., *Development of a comprehensive PMT optical model for the
   JUNO experiment* (2026).
   <https://arxiv.org/abs/2601.19081>

公开论文能够支持过程类型、总体量级和类型差异，但不公开JUNO全部逐PMT数据库、
完整运行标定或所有工程细节。本报告中“当前产生子状态”来自JunoResBench源码审计；
对未公开参数的具体分布不得伪装成JUNO测量，只能明确标注为synthetic modeling
choice，并通过最终release的物理图和统计门禁验证其观测后果。

## 16. 物理重要性、实现成本与双重计算负担筛选

### 16.1 筛选口径

本节不是把第3--10节的物理依赖缩减成开发TODO，而是回答两个不同的问题：哪些
缺失因素会实质改变当前的条件分布，以及应使用什么复杂度的替代模型保留它们。

筛选采用四个维度：

- **物理重要性**：该因素对均值非线性、分辨率、charge--time--position相关性或
  信息损失是否有实质影响；
- **实现成本**：需要修改的数据结构、传播算法和验证工具的相对复杂度；
- **生成负担**：HTCondor侧CPU、内存、逐光子状态和trace次数的增量；
- **使用负担**：release体积、I/O以及agent反复拟合和评测的增量。

评分为1--5，数值越大代表越重要或越昂贵。成本评分是根据当前向量化产生子结构
给出的工程估计，不是benchmark实测结果；任何具体倍率必须由小规模生产profiling
确认。成本只决定建模表达和实施顺序，不能用于否认已经确认的物理依赖。

三种可能的筛选方式中，“便宜优先”容易再次堆出装饰性噪声，“full-MC复刻优先”
会逐渐重写Geant4。这里采用**信息结构优先**：先保留有公开物理依据、并会改变
\(P(\mathbf W\mid E,\mathbf x)\) 结构的效应；再选择能保留该观测后果的最小物理
模型。

### 16.2 可以直接加入或修正的因素

这些因素重要性高、实现和计算代价低，或属于现有因果链的明确断点。

| 因素 | 重要性 | 实现成本 | 生成负担 | 使用负担 | 预期影响 |
|---|---:|---:|---:|---:|---|
| 保留最终光子入射方向 | 5 | 1 | 1 | 1 | 闭合传播时间、入射角与CE/PDE的关联 |
| 分离scintillation/Cherenkov光谱 | 5 | 2 | 1 | 1 | 恢复prompt光独有的波长和时间结构 |
| \(n_{\rm LS}(\lambda)\) 与群速度 | 4 | 2 | 1 | 1 | 产生波长相关TOF和可学习的时间PDF |
| HPK/NNVT/HQE分别使用CE曲线 | 5 | 2 | 1 | 1 | 形成类型--位置--电荷pattern |
| 类型相关QE谱 | 4 | 2 | 1 | 1 | 传播后的光谱变化影响不同PMT族 |
| 修正afterpulse时间结构 | 3 | 1 | 1或下降 | 1 | 去掉偏早、偏强的伪难度 |
| SPE谱使用测量支持的混合分布 | 4 | 2 | 1 | 1 | 保留NNVT大电荷尾和非高斯charge PDF |
| HPK/NNVT分别使用pulse模板 | 4 | 2 | 1 | 1 | pulse overlap和反演依赖PMT类型 |
| 加入约1%量级overshoot | 4 | 2 | 1 | 1--2 | 使固定积分窗产生时间结构相关偏差 |
| event/channel baseline漂移 | 4 | 2 | 1 | 1--2 | 固定pedestal不再能完全恢复积分电荷 |

“最终光子入射方向”不是可选增强。当前传播已经改变方向，却在PMT探测前重新使用
几何弦方向或初始Cherenkov方向，属于因果链断裂。修正本身几乎不增加计算量，并
恢复

```math
t_{\rm path}
\leftrightarrow \theta_{\rm incidence}
\leftrightarrow PDE/CE
\leftrightarrow Q_i.
```

光谱分离和色散对总能量均值的影响可能只有亚百分比，但它们的主要价值是让早光、
晚光、传播距离和PMT类型响应携带互补信息。类型模板、overshoot和baseline漂移不
增加波形长度，不过可能增加越阈ROI数量，因而使用负担需由实际release验证。

### 16.3 对条件分布结构影响最大的因素

这组因素实现成本较高，但最可能从根本上改变当前问题，而不是单纯把分辨率做差。

| 因素 | 重要性 | 实现成本 | 生成负担 | 使用负担 | 预期影响 |
|---|---:|---:|---:|---:|---|
| LS--acrylic--water三层传播 | 5 | 4 | 2--3 | 1 | 取代单介质球面传播 |
| 多界面Snell/Fresnel | 5 | 4 | 2 | 1 | 产生角度、波长和位置相关透反射 |
| 全反射TIR | 5 | 包含于上项 | 包含于上项 | 1 | 制造边缘非光滑响应和多路径 |
| acrylic/water吸收 | 4 | 2 | 1 | 1 | 增加真实路径相关光损失 |
| PMT光学反射和光子回流 | 4 | 3 | 2 | 1 | 未探测光子可延迟命中其他PMT |
| 节点/chimney/钢结构遮挡 | 5 | 3--4 | 1--2 | 1 | 打破纯径向和光滑球对称 |
| PMT光阴极落点响应 | 5 | 3 | 1 | 1 | PDE、TT、TTS和SPE共同依赖落点 |
| TT/TTS落点依赖 | 4 | 3 | 1 | 1 | 形成非平凡逐PMT时间PDF |
| SPE电荷谱落点依赖 | 4 | 3 | 1 | 1 | charge PDF与几何入射状态耦合 |
| pulse reflection/ringing | 4 | 3 | 1 | 1--2 | 产生与主pulse相关的延迟结构 |
| 有色电子学噪声 | 4 | 2--3 | 1 | 1--2 | 固定积分和简单阈值不再接近最优 |

多介质边界的重要性最高。对同心球无需通用几何导航，可以解析计算边界交点，在每个
界面执行Snell/Fresnel并自然得到TIR。初步估计Stage 3的生成时间会成为当前的
1.5--3倍，但ADC数据结构和窗口长度不变，因此不直接放大release。它预期产生的
核心观测结构是：

- 透射率随入射角和波长显著变化；
- TIR在边界产生局部光损失和长路径；
- 不同波长具有不同折射和传播延迟；
- 相邻顶点可能落在不同的光学传播分支；
- charge pattern和time pattern同时改变。

节点、chimney和钢结构的物理价值是固定地破坏球对称，而不是复刻机械细节。完整
CAD三角网格会产生接近通用ray tracing的成本；解析遮挡体或有物理几何依据的方向
透射mask可以保留主要 \((\theta,\phi)\) 后果，同时控制生成负担。这里允许简化的
是计算表达，不是空间非均匀性本身。

### 16.4 需要先由占用和波形量级决定的因素

| 因素 | 重要性 | 实现成本 | 生成负担 | 使用负担 | 需要验证的量 |
|---|---:|---:|---:|---:|---|
| PMT/前放饱和 | 3--5 | 3 | 1 | 1 | 边缘事件最近PMT的最大PE和峰值 |
| 高低增益双链路 | 3 | 3 | 1 | 1--2 | 是否实际进入增益交接区 |
| ADC DNL/INL | 2--3 | 2 | 1 | 1 | 14-bit量化下的相对贡献 |
| pre-pulse/late-pulse | 3 | 2 | 1 | 1 | first-hit尾部和顶点偏差 |
| dead/hot PMT | 3 | 2 | 1 | 1 | 固定mask能否被标定完全吸收 |
| 磁场方向依赖 | 3 | 3 | 1 | 1 | 公开锚点与预期方位响应幅度 |
| 逐管pulse模板差异 | 3 | 3 | 1 | 1 | 相比类型级模板的剩余方差 |

以饱和为例：1 MeV约1500 PE分散到17612个LPMT时中心占用很低，但边缘事件近端
PMT可能达到几十或上百PE。只有逐管最大占用进入实际非线性区，高低增益和饱和才会
改变1--10 MeV问题；否则它们只是额外代码。这里的“先验证”不是因实现困难而回避，
而是该效应的重要性本身由当前事件人口的占用量决定。

### 16.5 物理存在但直接加入可能改变题目性质的因素

| 因素 | 重要性 | 实现成本 | 生成负担 | 使用负担 | 主要风险 |
|---|---:|---:|---:|---:|---|
| 能损straggling | 3 | 4 | 1--2 | 1 | 增加径迹涨落，但能量收益可能有限 |
| delta rays | 3 | 4 | 2 | 1 | 需要受控截面和严格能量守恒 |
| 电子制动辐射 | 3 | 4 | 2 | 1 | 主要影响高能端，低能影响有限 |
| LS空间不均匀/时间漂移 | 3--4 | 3 | 1 | 1 | 容易变成人为domain shift |
| event pile-up | 3 | 3 | 1--2 | 3--5 | 大幅增加ROI和数据体积 |
| 放射性偶然本底 | 3 | 4 | 2 | 3--5 | 可能把任务转为本底识别 |
| 完整PMT薄膜transfer matrix | 4 | 5 | 2--3 | 1 | 参数约束困难，易产生伪精确 |
| 完整机械CAD ray tracing | 4 | 5 | 5 | 1 | 接近重写Geant4几何导航 |
| run-by-run gain/PDE漂移 | 3 | 3 | 1 | 1 | 需要重新定义标定与数据分区 |

这组因素继续保留在完整依赖图中。它们未进入直接增强组，不是因为计算困难，而是
因为其观测后果要么在当前能区相对次要，要么会把单事件能量--顶点重建变成domain
adaptation、本底分离或通用full-MC问题。

### 16.6 不构成有效增强的做法

以下做法即使成本很低，也主要增加不可约噪声或人为低维函数，不会自然增加可利用
的信息层次：

- 单纯增大白噪声或DCR；
- 把afterpulse人为提前到主信号窗；
- 任意放大逐管gain/PDE散布；
- 人工加入更高阶 \(f(r,\theta,\phi)\) 多项式；
- 无物理依据地压低PE yield；
- 随机删除波形或通道。

这些操作可能让3%目标变得更难甚至不可达，却不会证明产生子具有更丰富的可重建
结构。

### 16.7 手工径向响应的过渡地位

当前trace之后仍乘

```math
g(r)=1-0.15(r/R)^2+0.10(r/R)^4.
```

它的重要性高、移除成本和计算负担都很低，但不能在单球光学仍不完整时孤立删除。
若当前trace自然产生的非均匀性不足，直接删除反而会使问题更简单。目标关系应是

```math
\text{多介质边界 + 结构遮挡 + PMT光学}
\longrightarrow g(r,\theta,\phi)
```

自然涌现；确认其幅度和形态后，手工 \(g(r)\) 才失去存在理由。完整光学与手工径向
修正长期并存则会重复建模，并继续给低阶位置修正留下捷径。

### 16.8 三个需要闭合的核心机制

综合物理收益、实现成本和双重计算负担，最有价值的不是若干孤立效应，而是三个
因果闭环：

1. **光谱--传播--探测闭环**：最终入射方向、分离光谱、色散、类型相关QE/CE；
2. **多介质--边界--空间响应闭环**：LS/acrylic/water、Fresnel/TIR、结构遮挡、
   PMT反射；
3. **PE--pulse--ADC闭环**：真实SPE混合谱、类型模板、overshoot/ringing、baseline
   与相关噪声。

第一组使不同PMT不再只按统一效率交换；第二组使空间响应不再退化为光滑的纯径向
函数；第三组使ADC积分不再是PE数的近充分统计量。它们分别增强逐PMT charge模型、
复杂时间PDF和charge--time联合模型的研究价值，同时避免用无依据噪声构造难度。
