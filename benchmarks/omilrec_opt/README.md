# omilrec_opt — OMILREC v1.0.0 算法优化基准

对 JUNO 宏观似然顶点/能量重建算法 **OMILREC v1.0.0** 做单线程提速优化：
目标是在四道冻结正确性 gate 全 PASS 的前提下，把
`SPEED_MS`（SniperProfiling 单线程平均 ms/evt）降到 v1.0.0 基线
（~813.5 ms/evt，见 `repo/baseline/manifest.json`）以下。

这是从 `examples/omilrec_opt/`（上游开发包 `~/omilrec-v100-opt-pkg` 的
SimpleEvolution 集成副本）整理而来的 benchmark 版本，任务形态不变：
**生产算法优化基准**（冻结 gate + 性能基线），不是 toy-MC 数据集基准。

## 自包含性（重要）

v1.0.0 时代的版本依赖 `/data/juno/dingxf` 上的输入和 RecMap；本包已
**完全本地化**，不依赖 `/data`：

- 输入事件直接从 EOS 读：`repo/assets/inputs/index_12628_eos.json`
  （`root://junoeos01.ihep.ac.cn//eos/juno/juno-rtraw/.../RUN.12628...rtraw`，
  entry 0–49，需要 JUNO 网内 xrootd 可达）；
- 重建地图（nPEMap / TimePdf / ChargeSpec / CalibPMTPara，约 980 MB）
  全部随包携带在 `repo/assets/OMILREC_maps/`，4 个大 ROOT 走 Git LFS，
  SHA-256 由 `repo/tests/reference/manifest.json` 钉死。

唯一的外部依赖是 **JUNO 软件栈**（任何 IHEP 登录节点都满足）：

```bash
# 唯一硬依赖：/cvmfs 上的 JUNO 工具链（eval 脚本内部自动 source）
/cvmfs/juno.ihep.ac.cn/el9_amd64_gcc11/Release/J26.1.1/setup.sh
```

在 IHEP 登录节点（el9，自带 JUNO 依赖的系统库）上**无需 Apptainer**
即可裸跑；在不带 JUNO 依赖库的机器上，用 `apptainer.def` 构建沙箱镜像
（`setup.sh` 可代劳，或直接复用 SimpleLoop 的 `junosw-apptainer.sif`）。

## 快速开始（IHEP 环境）

```bash
cd benchmarks/omilrec_opt/repo
bash scripts/sl_eval_v100.sh --evtmax 10      # 冒烟（~4 分钟）
bash scripts/sl_eval_v100.sh --evtmax 100     # 完整 eval（~5-8 分钟）
```

前置一次性准备：

1. `./setup.sh` — 把 `repo/` 初始化成 git 仓库（本包已带 .git，会跳过），
   检查 /cvmfs，缺镜像时构建 apptainer.sif；
2. CVMFS Python 3.11 需要 pytest：
   `source /cvmfs/juno.ihep.ac.cn/el9_amd64_gcc11/Release/J26.1.1/setup.sh && pip install --user pytest`。

eval 输出结构化 token（退出码可信，不看日志措辞）：

```
CONTRACT=PASS          # 静态 gate 契约（gate 必须调生产 Calculate_EVLikelihood）
FCN=PASS               # 4 事件 × 4 Minuit 阶段, 相对 LL 误差 < 1e-13
CONSISTENCY=PASS       # 42 事件 E2E: ≤4 mm / ≤7 keV / ≤10 ps / ≤0.1 PE
SINGLE_THREADED=PASS   # OMP/MKL/OPENBLAS/ROOT 线程全部钉 1
SPEED_MS=891.62        # 目标（越低越好）
SPEEDUP_V100=...       # 相对冻结基线 813.53 ms/evt
EVAL_RESULT=ok
```

## 布局

- `repo/` — 优化目标（自带 git 历史，HEAD = `b7e463f`）。
  - `OMILRECV2/src/` — **唯一允许修改**的生产算法源码（v1.0.0）；
  - `tests/` — 冻结 gate（FCN golden 点、42 事件 E2E 参考 ROOT、SHA-256
    manifest）；`scripts/` — 冻结 eval/benchmark 脚本；`baseline/` —
    冻结 v1.0.0 性能基线（3×100 事件单线程，813.53 ms/evt 中位数）；
  - `assets/` — EOS 输入索引 + 全套 RecMap（Git LFS）；
  - `docs/RUN.md` — gate 与基线流程的权威说明。
- `reference/` — **隐藏的人类专家参考**（不要暴露给被测 agent）：
  v1.0.0 基线门槛、专家优化轨迹（~3-4× 提速）、known-safe/unsafe 优化目录。
- `setup.sh` / `apptainer.def` — 一次性初始化与沙箱镜像配方。
- `task.yaml`（可选）— 如需挂回 SimpleEvolution 框架跑进化，从
  `examples/omilrec_opt/task.yaml` 复制即可，`repo_path: repo` 不变。

## 版本契约

一切真值冻结自 OMILREC v1.0.0（commit
`b51f3b8d2f6c6562ce38dcb163ec4d0548031a33`）：生产源码、FCN golden 点、
E2E 参考 ROOT、性能基线。候选可以改生产代码，但**不得从自身重新生成真值**；
真值再生成（`scripts/generate_reference.sh`、`scripts/rebuild_baseline.sh`）
是基线维护操作，不是候选评估步骤。详见 `repo/CLAUDE.md` 与 `repo/docs/RUN.md`。

## 验证记录

- 2026-08-26：在 IHEP 登录节点（AMD EPYC 9654）裸跑（无容器）
  `bash scripts/sl_eval_v100.sh --evtmax 10`，从零 build 起，
  四 gate 全 PASS，`EVAL_RESULT=ok`，SPEED_MS=891.6（10 事件，
  基线为 100 事件口径，10 事件数偏慢属正常）。
