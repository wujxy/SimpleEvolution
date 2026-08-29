# JRB standard / oracle 两模式定案

2026-08-28 讨论。起因：白盒题把产生子源码随包发给 agent——产生子是正向模型的
完整规格，给了它任务就从"从观测反推世界"退化成"已知正向模型求逆"，测的是
逆向工程，不是建模。白盒 TASK.md 自己把话挑明了（"derive features from the
known CE(theta), eps(r) ... instead of estimating them from data"）——那句
话就是问题本身：它把建模从任务里删掉了。

## 定名

"白盒"一词收回重定义：**白盒 = 评分可见（eval 是怎么算分的可以看）**。
产生子是"世界机制"，不是任务规格——任务规格公开（否则是猜谜，分数不可
优化），世界机制隐藏（否则是抄答案）。两个正式名称：

| 模式 | 内容 | 目录（不改名，映射入档） |
|---|---|---|
| **standard** | 波形 + train/val 标定 truth + 评分器 + 题面（定义性信息） | `blind_task_*` |
| **oracle** | standard + 完整产生子源码 + 生成入口 | `whitebox_task_*` |

目录名保留是为最小改动：正在跑的 run、replay 链路、MANIFEST 都引用现名。
新生基建（`examples/junoresbench_full_std_opt`）起用 std 命名；其余包下次
重生成时按需跟进。

oracle 不删，但身份是 **ceiling 标定**：oracle 与 standard 的成绩差直接
度量"这题有多少分藏在建模里"。差距大 → 题在考建模；差距小 → 监督数据
已够，建模不重要。这是题目质量的自检指标，不是主评测臂。

## 删留清单（题面纪律）

一条线：**定义性信息保留，机制性信息删除**。

删（机制性——怎么产生的）：
- 触发模型整句（"fixed charge threshold on the summed detector rate"、
  抖动随 vertex/光统计/暗噪声）
- "trigger latency depends on the vertex — the two tasks are coupled" 耦合提示
- "uncorrelated dark-noise pulses" 噪声模型假设（波形里自己看得到；
  甄别任务的定义保留，术语中性化）
- MCP-PMT 技术细节、"toy" 字样
- README/spec 里的 design floor（~3%/√E、~10 cm、~1 ns）与所有
  "known CE/eps" 语言；bench.sh 注释里的同款数字
- "gamma escape tails"（mixed 任务遗留，electron 题无意义）

留（定义性——重建什么、怎么算分）：
- 几何与道数（17612、R = 19.37 m，文献级公开，重建必需尺度）
- 读出格式全套（零抑制、1 GSa/s、14-bit、1000 样本、wf_offsets /
  adc_pmt_ids、silent 不存）
- t0 参考点（sample 0 = trigger − 300 ns）与发光时刻语义——评分契约
- 事件群体（single electron，E 范围 train truth 可观测）
- truth 数组只含事件光电子（格式定义，措辞中性化）
- `t_run_s` 定义 + "不保证平稳"（背景级 due-diligence：只说要查，
  不说怎么漂；test meta 的 `drift: true` 与此同强度，不动）
- SANITY 反抄袭地板（harness 契约）、baseline 的 val 分数（跑
  bench.sh 可复现）

加：认识论声明段——产生链路不在包内；数据本身、train/val 标定、公开
文献是知识源；探测器响应模型要自己建自己验。

## 隔离契约（standard 模式）

- **mount 是边界**：runner `--cleanenv --userns --containall` + 显式
  bind；宿主盘上的正本产生子、`blind_truth_*` 容器内不可见
  （smoke S9 已检查）。
- **模板卫生**：`$NODE_TEMPLATE:/repo:ro` 这条 bind 把模板整个暴露给
  agent——standard 模板里不得出现产生子（`junoresbench_full_std_opt`
  的 repo 只带盲盒包）。
- **eval 侧纪律**：评分代码不得包含 test 真值的任何充分统计量
  （evaluate.py 已审计干净：truth 全走 `--data` 传入）。
- **文献能力对齐**：两臂共享容器网络，检索能力天然对齐；不为某一臂
  单独接文献工具。
- 历史教训（xsbench v3 探针兄弟目录泄漏）继续有效：探针 run 隔离邻域。

## 现有 run 的身份

runs/singlenode 下四个 jrb run（jrb-wb-elec-nolimit-{scientist,coding}、
jrb-full-elec-nolimit-{scientist,coding}）world 里都装着 whitebox 包
（/repo:ro 还暴露第二遍）——全部是 **oracle 模式测试数据**，可作 ceiling
参照，不进主结论。

已知 bug：jrb-full-elec-nolimit-scientist 的 spec goal 是从 wb 任务抄来的
旧文本（引用 `benchmarks/whitebox_task_electron`、描述"random channel
subset + pmt_offsets"——那是 192 通道子采样题，不是 full readout）。
coding 臂 task txt 是对的。world README 是对的。当测试数据看，不修在跑
的 run；std 模板的 spec 已改正。

## 题面形态纪律（2026-08-28 夜补充，用户拍板）

**否定式列举也是泄漏**：说明书里写"我们不提供 X/Y/Z"，agent 立刻知道
这套世界存在 X/Y/Z 这套参数化；"建模是你的活、拿标定数据验证、查文献"
是教科研流程。standard 题面必须长得像一份**普通数据集文档**：开题背景
一两句、数据格式、任务、评分——不声明缺什么、不教怎么做。已按此清洗：
TASK.md 认识论声明段、spec goal 的 "modeling...are your job"、README 的
"are the work"、solve.py docstring 的 STANDARD 段全部删除。

**PMT 出厂测试表随包给**（`pmt_datasheet.md`，型号级）：真实 JUNO 的
1.7 万支管子每支有 mass test，第一次接触数据的人手里就有标称表——不
给它，agent 得猜"该测哪些量"，是格式考古不是科学。实现成信息两级而非
人为扭曲：型号级 typical（取产生子真值中心，datasheet 口吻）+ 通道间
uniformity 容差行（正是真 spread：gain 15%、效率 8%、时偏 1 ns）——
"飘"由物理 spread 天然产生，as-run 值就是刻度问题。**不给 per-tube 表**
（会替 agent 解决 per-PMT 刻度先验中心，削弱 electron_full 的核心难度
轴）；**不出现任何"真实值需自行刻度"式指导句**，nominal 一词自己说话。

**检索面不配线**：coding 臂=claude 自带搜索 MCP，scientist 臂=自带
searcher；两臂天然对齐，不另接工具。（2026-08-28 首跑前实测闭环：
ds 后端 WebSearch 通——服务端执行，SEARCH-OK、零拒绝；唯一卡点是
`-p` 模式白名单外工具一律拒绝，`run_coding.sh` 的 allowedTools 已补
`WebSearch,WebFetch`，与 scientist executor 白名单 assistant_tools.py
对齐。未接任何新 MCP、未动网络。）
**知识源不列为给定**：网络是环境属性、参数记忆是 agent 本钱，
题面只给开题背景（JUNO 是干什么的级别）。

**开题背景=叙事，不是格式说明**（2026-08-28 深夜第二条）：scientist
要从背景里理解项目、才知道搜什么——所以背景要讲故事：JUNO 是什么
（江门、地下 700 m、质量顺序、IBD、2 万吨 LS）、物理图像是什么
（带电粒子穿越 LS 沉积能量，切伦科夫光+延迟闪烁光，光传播到光阴极，
每个光电子成为一道脉冲，电子学数字化）。**线在"教科书第一章/综述
级叙事给，本世界具体参数化不给"**：故事零数字参数（几何除外）、零
触发/噪声/漂移机制；切伦科夫既是教科书成分也是本产生器真实成分
（s2 Cherenkov 分支 default ON）。TASK.md 三段背景 + spec/coding/
README 各配浓缩一句。

**终审修复（2026-08-28 深夜 review）**：元信息泄漏一组——模板内包
目录原名 `blind_task_electron_full`、"This is the STANDARD task:"、
README "standard mode"、spec node_id 带 std，都在暗示"这是多模式
benchmark 的一种、别处有隐藏信息"。修法：模板内包目录改中性名
`benchmarks/electron_full/`（正本宿主侧名字不变），全部 mode 字样
从 agent 可见面清除。另修 `replay_jrb_wb.py` 硬编码
`glob("whitebox_task_*")` 对 std 模板跑不通的 bug（现两种名都认）。
已知良性行为：std 基线 timing 比 oracle 差 ~0.1%（σ 估计 5.93 vs
真值 5.73 → leading-edge 阈值略保守；energy/vertex 逐位相同）。

## electron_full 先行

standard 基建只做 electron_full（realism arm，新基建都在）：
`examples/junoresbench_full_std_opt`。其余包（electron、electron_static、
mixed）按同配方后续跟进。
