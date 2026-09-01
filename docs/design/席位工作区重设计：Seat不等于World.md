# 席位工作区重设计：Seat ≠ World

2026-09-01。上游：身份定稿（423b39b，三层责任结构）+ xsbench-v4 活跑
照出的 reviewer fork 递归虫（dc0fbe9 补丁）+ 提案 agent 诊断
（docs/chat/2026.9.1.15.21.gpt聊席位工作区设计.md）+ 执行 agent 的
三个工程补丁。本文是定案设计，未实现。

---

## 一、动机：三个维度被绑死了

现实现把三件本该独立的事绑在"角色"上：

1. 你是谁（Proposer/Challenger/Reviewer/Searcher/Executor）
2. 你能看什么（live world / memory / ledger / external）
3. 你能改什么（current / isolated / none）

角色一换，拓扑就换。PI 学到的是"开 Challenger = 开一种 Challenger
世界"，而不是"我缺反方观点，所以叫 Challenger 来看**同一个**研究问题"。
行为证据：xsbench-v4 的 challenger 全程在读 benchmark 算 EV——没用到
它的 fork 世界；我们为分析活儿付了世界拷贝。递归虫的成因史同一根：
先因安全给 fork，再因职责塞账本，两刀叠出"身体在日记本里"。

**safety boundary 不该升格成 agent ontology。**fork 的原始理由（带
shell 的席位不能碰活世界）是工程安全，后来长成了角色世界观。

## 二、核心原则

> **Seats do not get worlds. Experiments do.**

席位是认知职责；世界是研究对象。资源由**行为需要**决定，不由角色
名称决定。三种资源，不是五种角色世界：

| 资源 | 语义 | 谁用 |
|---|---|---|
| **Live World** | 当前现实（accepted research world） | 人人可读；**只有 mainline executor 可写** |
| **Scratch** | 临时思考（脚本/reproducer/分析），不代表候选世界 | 每个席位都有，可弃 |
| **Experiment World** | 可弃的 alternative reality，**行为驱动创建** | 谁需要验证 world-changing 假设谁用 |

## 三、各席位的新形状

- **Scientist**：站在研究现场（live world + reports + memory + ledger）。
  无专属世界——现状不变。
- **Executor · mainline**：workspace = live world，持续改 accepted
  world。**现实现原样保留**（current 语义即 mainline ownership）。
- **Executor · speculative**：workspace = 预建实验世界。简报本身已决定
  "去验证 alternative world"，故 harness 预建（现 isolated 语义原样
  保留；报告带 diff、PI apply 的回路不变）。
- **Proposer / Challenger / Searcher**：**live world 只读接触 + 自己的
  scratch + 实验工具包**（§五）。不再自动 fork。
- **Reviewer**：live world 只读 + 研究记录只读（wire/judgments/reports/
  memory——它的工作对象就是历史）。**带账本 fork 特例整个蒸发**，
  递归虫的生态位消失。

认知席位不是 PPT 顾问，是 researcher。能力是**渐进阶梯**而非角色
权限矩阵：

1. 直接读活世界（grep/读码/看 diff/跑现有 benchmark 与 tests/查历史）；
2. 自己的 scratch（写 Python/microbenchmark/编译 reproducer/做计算）；
3. 需要改项目代码时——**自服务创建实验世界**（§五工具包），改、编译、
   测量，报告带 evidence + diff 回来。

**角色由为什么做定义，不由敲不敲代码定义**：Challenger 为攻 claim
写 50 行 counterexample 仍是 Challenger；Proposer 为验想法做 cheap
prototype 仍是 Proposer。Executor 的特殊性不是"只有它能写代码"，而是
**它可以拥有 sustained world-changing research**（charter 级的持续
追求）。

**边界语义**：cognitive seat = investigate for a particular epistemic
purpose；executor = own sustained pursuit of a research charter。
自然概念：**probe → stretch promotion**——席位实验长出值得持续发展的
线时，PI 判断"this deserves sustained ownership"，升级为 executor
stretch。晋升判断归 PI（三层结构的 program-level 权力，不动）。

## 四、新目录语义

```
.scientist/          = mind / record（只存研究认知与记录）
    wire/ judgments/ memory/ reports/
<scratch 挂载区>/    = 席位身体与工作台（运行时细节，非记录）
    seats/<id>/          session、raw、digest、席位 scratch
    seats/<id>/exp-NNN/  席位自发创建的实验世界
    experiments/<id>/    speculative executor 的预建实验世界
```

- 容器几何：scratch 区 = /scratch 独立挂载（现状即如此，r5/JRB 从未
  踩虫的原因）。
- standalone 几何：scratch 区 = **run 目录下的 `seats/`**（世界外、
  run 内），彻底移出 .scientist。递归虫的几何前提消失。
- `_ship_memory` 删除：席位直读活世界里的公共知识层
  （research_memory.jsonl），无需随叉上船。
- 私有思维流（wire/judgments）对认知席位仍是 PI 的私有层——靠 prompt
  规范句声明（"记录的私有通道属于 PI；memory 是公共知识层"），reviewer
  显式授予。执法尽力而为（§七风险）。

## 五、三个工程现实的解法（提案方挥手处）

**① 只读接触怎么落地。**认知席位是带全套 bash 的 claude 进程，指哪
都能写；standalone 几何没有便宜的只读。方案：**不执法，降爆炸半径**——
(a) 活世界是 git 库，席位乱写=脏树，可恢复；(b) 席位发射时 cwd = 它的
scratch，活世界以路径形式"可读"；(c) 真要改世界的需求被工具包引到
可弃副本里；(d) 账本侧缓解见③。容器几何下未来可换 :ro 挂载，语义不变。

**② 中途资源请求不存在 → 实验工具包自服务。**席位是一次性 claude -p
进程，跑中无法向 harness 要资源。方案：发射认知席位时在其 scratch 里
放置 **`make-experiment` 脚本**——内容即现 `_fork_world` 逻辑的下沉
（小树真拷、`benchmarks`/≥512MB 大目录 symlink 回只读原件、忽略
.scientist）。席位何时真要改世界验证，自己跑一下得到 `exp-NNN/`，
随便折腾，diff 随报告回家。**Experiment world 由行为在运行中产生**，
提案的语义用现有机器兑现，无需新通道。

**③ fork 顺带保护了账本，新设计暴露它。**wire/memory 现在对席位物理
不可达（从不入叉）；新设计下行为不端的席位理论上可写记忆文件。风险
评估：全部活跑零事故；爆炸半径=整场 run 的记忆。缓解：快照环本来就
冻结 body（.scientist 减 raw.txt 与 >2MB 大件，每内容变化一拍——实现
时核正，原设计稿"只拍 src"记错），记忆与 wire 可从快照恢复。

## 六、存亡清单

| 现机制 | 命运 |
|---|---|
| Executor `current` | **活**（= mainline ownership，语义原样） |
| Executor `isolated` | **活**（= 预建实验世界；diff→PI apply 原样） |
| 认知席位自动 fork（含 `_FORK_NOTE`） | **死** → 三资源模型 |
| Reviewer `include_ledger` 特例 | **死**（整个分支删除；直读记录） |
| Searcher `read: none|node|lab` enum | **死**——意图进 brief（"只从文献答"/"看原始实现"） |
| `_ship_memory` | **死**——公共知识层直读 |
| `_fork_world` | **变形**——下沉为席位 scratch 里的 make-experiment 工具包 + speculative executor 预建共用同一函数 |
| continue 复用旧工作区 | **活**——路径从 .scientist/scratch 改到 seats/，语义不变 |
| 快照环 | **已达标**——body（.scientist）本就随拍，实现时核正 |

## 七、风险与可回退性

- **席位写活世界/写记录**：信任前提（零事故史）+ git 恢复 + 快照缓解
  + prompt 规范句。若 live 判据出现滥用，回退路径：容器几何 :ro 挂载，
  或恢复认知席位 fork（本设计的反例即旧设计，可退）。
- **认知席位实验质量**：scratch 里的工具包副本与活世界可能漂移（活世界
  在动）——工具包按需即时拷贝，漂移窗口=席位会话时长，可接受；报告
  带实验世界的 git 基线 sha 以便 PI 判读。
- **工具载荷变化**：read enum 与 fork 语义参数消失，预期净减；实测
  词数入验收。

## 八、实现顺序（定案后）

1. 快照环扩 .scientist（安全网先行）
2. scratch 区迁移：席位家 .scientist/scratch → run/seats/（continue
   路径、resume 兼容同步改）
3. make-experiment 工具包脚本（_fork_world 下沉；speculative executor
   预建共用）
4. 认知席位 launch 改形：cwd=席位 scratch、prompt 三资源句 + 工具包
   告知、_ship_memory 删
5. reviewer 直读记录（include_ledger 分支删除）
6. searcher read enum 删（工具 schema 与 NATIVE 描述同步）
7. 测试改造 + 工具载荷词数复核

## 九、验收

单元/冒烟：make-experiment 脚本行为（symlink 大目录/忽略 .scientist/
带基线 sha）；launch 形状（认知席位无 fork 目录、cwd=scratch）；continue
复用 seats/ 路径；reviewer 无 fork。

live 判据（下一次 xsbench 类活跑）：
1. 认知席位全程零 fork 目录，除非自发用了工具包；
2. challenger 直接读活世界完成分析（对照 v4 的 fork-challenger）；
3. **至少一例席位自发创建实验世界**（工具包被用——渐进阶梯第三层
   真的发生）；
4. reviewer 直读记录完成回望，无递归可能；
5. executor current/isolated 与 continue 行为无回归（r5 好行为）；
6. 工具载荷：**重设计自身只减不加**（read enum 移除、无新增参数）。
   实测 25 工具 2300 词——较 v4 前基线 2106 的 +194 全部来自 v4 已付
   租金的语义句（fuse 从句×6/executor 整目标/View 态势），非本设计
   产物；fuse 参数描述已瘦至最短诚实形。

---

## 十、live 验收记录（2026-09-01，双跑）

sew 跑（runs/xsbench-2h/scientist-sew，wall 7200s，自 deliver 于
step 191）+ shakedown 跑（runs/xsbench-2h/shakedown，wall 5400s，
goal 即工作区七件套自查，自 deliver 于 step 88）。**六判据 6/6 绿**：

1. 零自动 fork：两跑 10 席位发射，认知席位全 mode=scratch；
   seats/ 下唯一世界=isolated executor 预建。
2. challenger 直读活世界：transcript 内 24 处活世界路径引用
   （Simulation.c×6、GridInit.c×2）；报告本体死于 ds 429（infra）。
3. **席位自发 make-experiment ×6**：sew searcher（复现前人工作于
   自建 exp-001，7.43M 过闸后才报告）、sew reviewer（自建 exp 重跑
   闸门×3 + 从真 baseline 建工作树验 15 长度 bit-identity 15/15 +
   甄别灰色变体）、shake searcher×1、shake proposer×3——渐进阶梯
   第三层是认知席位的常态工作方式，不是边缘路径。
4. reviewer 直读记录：sew reviewer 无 fork 无账本特例完成全深度
   回望审计（出处 git-archive diff、闸门复跑、越闸验证、合法性甄别）。
5. executor/continue 无回归：current 直写活世界（2 commits）；
   isolated 003 超盒打捞（fuse 语义）→ **续用为 004**（session+
   workspace 复用，manifest 佐证）；续用产物 C1+C2 "validated but
   NOT promoted"——diff 回报、PI 判断不合并，④句场景真实发生。
6. 载荷：如上，未增。

附带读数：①PI 在 junction 的诚实度——429 杀 reviewer 后，conclusion
warning 显式声明"frontier 未获 reviewer 背书，请独立验证"；②sew 主
战役 PI 全程 solo 未开 executor（7.2M 自己打满）——正是 v5 文本轮
"delegatability 自问"要松的反例；③searcher 在磁盘上找到前人 v4 run
并复现后移植——入世无沙箱下的合法研究行为（发现→自建世界复现→
带证据报告→PI 判断），非泄漏事故；④**ds token 5h 用量窗是硬墙**：
双跑并行 18:10 烧穿，四席位 429 陪葬——**r6 必须单跑**。

v5 文本轮同日落地（见 memory/commit）：_COLD_START 的排队句替换为
delegatability 自问+分叉；delegation.md 落 "Share what you learned,
not the path you would take from it" + hypothesis-status 段与
"returns evidence and a candidate world, not an obligation to merge"。
