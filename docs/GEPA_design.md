我觉得你现在回头看 GEPA 的 frontier 很合适，因为 **GEPA 真正值得借的不是 reflection，而是它怎么避免“永远从当前 best 往下长”**。而且它的做法其实比我们前面讨论的 Reviewer 简单很多：**不判断 proposal 好不好，只决定下一次从哪些已经存在的 candidate/node 继续演化。**

先说一句最关键的：

> **GEPA 的 Pareto frontier 不是通常意义上的“保留 top-N 个最好 candidate”，也不是泛泛的“保持多样性”。它保留的是：在不同 evaluation instances 上各自有优势、因而仍然代表某种 winning strategy 的 candidates。**

这是理解它的核心。论文明确对比了 greedy best、beam search 和 Pareto sampling；只追 global best 会很快形成一条窄链，而 Pareto sampling 产生更均衡的搜索树。

---

## 1. GEPA 最基本的演化结构

GEPA 一开始只有一个 candidate：

```text
P0
```

然后循环做：

```text
Candidate Pool
      ↓
选择一个值得继续演化的 parent
      ↓
Reflective Mutation
      ↓
Pnew
      ↓
小批量 evaluation
      ↓
如果比 parent 有改进
      ↓
加入 Candidate Pool
```

每个新 candidate 都记录 ancestry，所以实际上天然形成一棵 genetic tree。论文算法里明确保存 candidate pool `P` 和 parent records `A`。

这里和你现在的想法其实已经很像：

```text
Node
↓
Proposer
↓
Proposal
↓
Executor
↓
Node
```

区别只是 GEPA 的“mutation”本身直接产生新 candidate，而你把 **thinking 和 reality experiment** 分成了 Proposer / Executor 两层。

---

# 2. 真正关键的是：新 candidate 出来以后，下一步从谁继续长？

最简单的方法当然是：

```text
所有 candidates
↓
选 objective 最好的那个
↓
继续 mutate
```

也就是 SimpleLoop：

```text
N0 → N3(best) → N8(best) → ...
```

GEPA 论文专门做了这个 ablation，发现 SelectBestCandidate 很容易快速卡在局部最优；BeamSearch 保留 top-N 也依旧容易陷进去。相反 Pareto candidate sampling 会从多个不同 candidate 继续扩展。

所以 **GEPA 的核心不是“Tree”本身**。

Tree 谁都可以存。

它真正解决的是：

> **树已经长出来以后，有限的下一次 evolution budget 应该投给哪个 parent？**

这正好就是你现在缺的 Node Frontier。

---

# 3. GEPA 的 Pareto 到底怎么算？

这个地方非常容易误解。

GEPA 有一组 validation instances：

```text
Task 1
Task 2
Task 3
...
Task N
```

每一个 candidate 都有一个 score vector：

```text
       T1   T2   T3   T4
P1     .8   .5   .7   .6
P2     .7   .9   .6   .7
P3     .6   .6   .9   .8
```

然后 GEPA 先问：

> **每一个 task instance 上，谁是目前最好的？**

例如：

```text
Task 1 → P1
Task 2 → P2
Task 3 → P3
Task 4 → P3
```

那么：

```text
P1
P2
P3
```

都有继续存在的理由。

因为它们各自在某些问题上表现出了“winning strategy”。论文算法就是先构造每个 instance 的 best-candidate set，再从它们的并集中去掉被支配 candidate。

所以这里的 Pareto 并不是经典的：

```text
latency vs accuracy vs memory
```

这种多指标 Pareto。

而是非常巧妙地把：

> **不同 training/validation instances 当成不同 objectives。**

---

# 4. 然后怎么从 Frontier 里选 parent？

也不是：

```text
Pareto frontier 内全部平等随机
```

GEPA 会统计一个 candidate 在多少个 instances 上属于 best。

例如：

```text
P1 best on 3 instances
P2 best on 8 instances
P3 best on 1 instance
```

那么它们都还可以被选中，但：

```text
P2 被采到的概率更高
P1 次之
P3 仍然有机会
```

Algorithm 2 里明确把这个频次记为 `f[Φ]`，再按 `f` 成比例随机采样。

这非常重要，因为它同时完成：

```text
Exploitation:
好的、广泛有效的 candidate
→ 更经常继续演化

Exploration:
只在少数 instance 上特别好的 candidate
→ 不会被 global average 直接杀掉
```

这就是它所谓 Genetic-Pareto 的核心。

---

# 5. 一个很直观的例子

假设有三种题：

```text
             数学   检索   指令遵循   Mean
A            95     60      65       73
B            80     90      70       80
C            75     70      95       80
```

如果只看 mean：

```text
B/C > A
```

A 很可能被扔掉。

但 GEPA 会看到：

```text
数学 → A best
检索 → B best
指令 → C best
```

于是：

```text
Frontier = {A, B, C}
```

A 虽然综合分低，却保留了一种其它 candidate 没有的能力。

后面：

```text
A → A1 → ...
B → B1 → ...
C → C1 → ...
```

所以 GEPA 的 diversity **不是靠“这个 candidate 看起来和别的不一样”来定义的**。

而是：

> **它在现实 evaluation 上表现出了别人没有覆盖的优势。**

这一点我很喜欢。它不需要 Reviewer 来判断“这个 branch 新不新”。

---

# 6. 这和普通 Beam Search 区别非常大

Beam search 是：

```text
所有 candidate
↓
aggregate score 排序
↓
留下 top K
```

比如：

```text
B 80
C 80
D 79
E 78
```

全部可能是同一种 strategy。

所以即使：

```text
beam width = 10
```

也完全可能得到十个几乎相同的局部最优。

论文也专门报告了 BeamSearch(N=4) 仍明显弱于 Pareto sampling。

GEPA 则是：

> **不是限制 population 数量来制造 diversity，而是保留不同 evaluation niches 的 winners。**

这个思想比“frontier size=N”更深。

---

# 7. 还有一点特别适合你的设计：GEPA 没有“一个 permanent frontier”

严格来说它有：

```text
Candidate Pool P
```

保存所有已经接受的 candidate。

而 Pareto frontier 是根据当前 score matrix **动态算出来的 selection view**。

也就是说：

```text
Archive ≠ Frontier
```

更准确：

```text
Archive / Candidate Pool
= 我们已经发现过什么

Frontier
= 当前哪些 candidate 仍然值得获得 evolution budget
```

这个 distinction 对你特别重要。

你现在的 SQLite 可以天然保存：

```text
所有 Nodes
```

也就是 Archive / Research Tree。

Scheduler 不需要删 Node。

它只是每次需要分配 Proposer capacity 时，从所有 Nodes 中维护/计算：

```text
Active Frontier
```

然后从 Frontier 取 parent。

所以：

> **Tree 是历史结构；Frontier 是资源分配视图。**

这两个东西一定不要混。

---

# 8. GEPA 的 Tree 也不是“每个新 candidate 都继续长”

论文 Figure 6 特别直观。

Greedy：

```text
root
  ↓
best
  ├────────────── 一堆尝试
```

很快坍缩。

Pareto：

```text
        root
      / | | | \
     A  B C D  E
     |    |\    |
    A1   C1 C2 E1
         |
        C3
```

有多个 lineage 获得继续演化的机会。论文把这种结果直接描述为更 balanced 的 search tree。

所以如果映射到你现在的 Harness，我会修正我们之前的：

> “每个新 Node 默认获得一次 proposer。”

**GEPA 给了一个不同、而且我觉得更值得考虑的答案：**

> Node 可以全部进入 Tree，但并不是每个 Node 都必须获得 Proposer；有限的 Proposer budget 从 Frontier 中分配。

这才是真正意义上的 frontier maintenance。

---

# 9. GEPA 还有 Reflective Mutation，但这个和你目前并不完全一样

GEPA 选中一个 candidate 后，会执行它、获取：

```text
execution trace
evaluation trace
numeric score
text feedback
```

然后 Reflection LM 分析这些反馈，修改 prompt。

它的核心思想是：

> 不只把一次 rollout 压缩成一个 reward，而要利用 compiler error、tool output、reasoning trace 等丰富自然语言反馈来指导 mutation。

这和你的 Scientist 很像，但你的 Scientist 比 GEPA mutation agent 更开放：

```text
GEPA:
parent
→ 看 rollout failure
→ targeted mutation

你的 Scientist:
Node
→ 自己调查 world / history
→ 建立理解
→ 提出多个 proposal
```

所以我不会去复制 GEPA 的 mutation mechanism。

**真正值得借的是它的 parent selection / frontier idea。**

---

# 10. GEPA 还有 Merge，但我建议你现在完全先放一边

GEPA 的 merge 是把具有共同 ancestor、但分别改进了不同模块的两个 descendants 进行 system-aware crossover。例如一个 descendant 改了 module A，另一个改了 module B，那么可以把两边互补改动组合起来。论文还特别加了 lineage 条件，使 merge 实际发生得比较稀疏。

这个在 modular prompt system 上比较自然：

```text
Parent
├── child A: 改 prompt module 1
└── child B: 改 prompt module 2

→ merge A+B
```

但在代码 Research Tree：

```text
N8 branch
N9 branch
```

两个 patch 是否 composable 是完全不同的问题。

所以我不会把 merge 放进你这个 MVP。

---

# 11. 但是这里有一个非常重要的问题：**GEPA 的 Pareto 不能直接照搬到 OMILREC**

这可能才是我们下一步真正要讨论的。

GEPA 能这么做，是因为它有：

```text
candidate × validation-instance score matrix
```

例如：

```text
P1 在 task 17 最好
P2 在 task 38 最好
P3 在 task 91 最好
```

因此有天然的 niches。

但是 OMILREC 如果最终每个 Node 只有：

```text
SPEED_MS = 310
```

一个 scalar objective，

那么 Pareto 会退化成：

```text
310 < 320 < 350

Frontier = 310
```

也就是重新变成 best-only。

所以不能简单说：

> “GEPA 用 Pareto，我们也搞 Pareto frontier。”

**关键问题不是 Pareto 算法，而是：你的 Research Node 有没有多个独立、真实、具有研究意义的 performance dimensions/niches？**

---

# 12. 例如你可能天然已经有一些 candidate-wise dimensions

不是让我现在拍脑袋给你设计，我只是举例说明 GEPA 的要求：

如果 Eval 本身能够产生：

```text
event 1 runtime
event 2 runtime
...
event N runtime
```

理论上可以：

```text
N17 在某些 event 最快
N23 在另一些 event 最快
```

那么就能像 GEPA 一样维护 instance-wise Pareto。

但这里得谨慎：

> 不同 event 上偶然快一点，究竟代表真正不同的 optimization strategy，还是 measurement noise？

如果只是噪声，这种 Pareto 会制造大量垃圾 frontier。

又比如：

```text
single-thread speed
multi-thread speed
memory
```

也可以形成多目标 frontier，但前提是它们真的都是 Goal 所关心的 dimensions。

**不要为了 Pareto 而制造 objectives。**

---

# 13. 所以我觉得 GEPA 给我们的真正启发不是“照抄 Pareto”

而是这一条：

> **不要问“哪个 Node 是全局最好？”；应该问“哪些 Node 各自代表了目前仍未被其它 Node 支配的、有现实证据支持的演化机会？”**

GEPA 恰好可以通过 per-instance scores 非常机械地回答这个问题。

我们需要找的是：

> **你的科研任务里，什么构成这样的 objective/niche？**

如果找不到，那就不要硬套 Pareto。

届时可能更适合一个更简单的：

```text
Tree Archive
+
固定大小 Frontier
+
objective + branch diversity / novelty
```

但那已经不是 GEPA 原算法了，我们需要明确这是自己的 evolution policy。

---

## 我现在会这样看你整个系统

把 Reviewer 拿掉以后，反而更接近一个干净的 GEPA-like evolution：

```text
                   Research Tree / SQLite
                           │
                      all Nodes
                           │
                  Frontier Selection
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
             N17                      N42
               │                       │
           Scientist               Scientist
               │                       │
          proposals                 proposals
               │                       │
          Executors                 Executors
               │                       │
        new child Nodes           new child Nodes
               │                       │
               └───────────┬───────────┘
                           ▼
                    Research Tree
```

于是 **Scheduler 真正唯一有“演化算法味道”的地方，就是 Frontier Selection。**

Proposer 仍然做 Scientist。

Executor 仍然让现实给结果。

数据库仍然保存全树。

这比加一个 Reviewer 要干净很多。

---

我觉得我们下一步最应该讨论的，不是“GEPA frontier 怎么实现”，而是先回答一个更基础的问题：

> **在你的 SimpleEvolution / OMILREC 这类任务里，GEPA 用来定义 Pareto niches 的“per-instance performance”有没有自然对应物？如果没有，我们应该用什么原则定义 Frontier，才能既不退化成 best-only，又不靠 LLM Reviewer 主观维护 diversity？**

这个问题一旦回答，Node Scheduler 基本就定型了。