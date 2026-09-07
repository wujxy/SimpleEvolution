# JunoResBench 发布手册（publish runbook）

目标：**产生子升级 → 题库重新发布 ≤ 1 小时**；波形树的发布与验收解耦，
验收失败永不阻塞/改写已发布的切片数据。

原则只有三条：
1. **切片即发布**——波形只在生成作业里写一次，之后零拷贝、零重写，release 树里是软链。
2. **完整性下放到片**——每个切片作业结束时自己算好 sha256/字节数写进片内 manifest；
   发布时信任它并抽查，绝不全库重哈希。
3. **先验证后删除**——旧数据在 ACCEPTED 且逐字节核对前一个都不删。

---

## 链路总览

```
产 PRODUCE ──► 发 PUBLISH ──► 收 ACCEPT ──► 清 CLEANUP
 (condor 数组)   (分钟级簿记)    (独立可重跑)   (ACCEPTED 后)
```

| 阶段 | 耗时 | 触发条件 |
|---|---|---|
| 产 | ~3h（240 路并行，墙钟） | 波形物理/电子学变了 |
| 发 | ~5 min | 任何升级（改波形时排在产后） |
| 收 | ~10 min | 每次发布后；也可随时单独重跑 |
| 清 | 按需 | ACCEPTED 且确认后 |

改 scoring / gate / oracle / 题面 / 评测配置 → 只跑【发】【收】≈ **15–20 分钟**。

---

## 产 PRODUCE（切片生成数组）

脚本：`world_generator/condor/submit_array_v21.sh` + `run_shard_v21.sh`

- 240 片，`hep_sub -g juno -os AlmaLinux9 -np 1 -wt short`。
- **提交前必须 `export JRB_REPO_ROOT=...`**——wrapper 对它做了硬校验，
  漏了会 27 个作业秒挂、日志一行报错（本次实际翻过车）。
- 落盘分配按配额拆：`0-149 → scratchfs2`，`150-239 → junofs`（各自 500G 上限，
  单盘装不下全库）。
- 每片结束（shard_generate.py）：
  - `_finalized()` 校验"index.npz + payload 字节数一致"才算成品，
    残缺目录直接删除重生成（ENOSPC 暗伤的根源堵点）；
  - 对本片三个 split 计算 sha256 与字节数写入 `shard_manifest.json`；
  - 逐文件 fsync 后才写 manifest（防"manifest 先可见、内容后到"）。
- **交完作业 2 分钟内必须确认作业真的开工了**：看 worker 侧日志/目录有无新文件。
  日志 0 字节 + 队列里消失 = 立刻查（env、挂载、quota），不要等。

## 配额纪律（每次动盘前必查）

```
lfs quota -u lidian /lustrefs/juno26 /junofs /scratchfs2
```

- lustre 的 quota 统计**有滞后**：删 200G 可能几小时后账面才回落，
  也可能突然回冲导致满配额 EDQUOT。大写入前后各查一次，写入中预留 >15% 余量。
- 三个盘任何一个 >90% 时禁止新开批量写入。
- `rsync --files-from` **隐含 -d（只建目录不递归）**——拷目录树必须显式 `-r`。
  本次曾因此"拷完 150 片只有 612K"。
- 大迁移用 6 路并行 rsync（实测聚合约 800MB/s，203G ≈ 5 分钟）。

## 修 REPAIR（切片损坏的定位与重放）

适用症状：发布门禁报 payload 0 字节/字节不符、片缺某个 split、manifest 与磁盘不符。

1. **全量扫雷**（不要等门禁一片一片爆）：对 N 片 × 3 split 逐个
   "index 声明样本数 × 2 == payload 文件长 − npy 头长"校验，列出坏片清单。
2. **确定性重放**：同 seed 下 populations、探测器种子、事件种子全部可复现。
   片内 split 按序仿真（calibration → dev → final），重放被毁 split 前必须先
   重放它前面的 split（保持模拟器 RNG 状态），后面的 split 不影响、可跳过。
   重放产物与"当初没崩的话"逐比特一致。
3. **staging-then-move**：修复作业写独立 staging 目录（同盘或空余盘），
   登录节点核对字节后 `cp -a` 进片的家目录，最后删 staging。
   不要让修复作业直写发布树。
4. 修复作业同样遵守"2 分钟确认开工"与 env 纪律。

## 发 PUBLISH

脚本：`world_generator/condor/publish_release_v21.sh`
（零波形拷贝：真相/标签/配置/manifest 等簿记 ~100MB + 软链树）

- `publish_release.py --publish-shards`：
  1. 逐片读片内 manifest 的 sha256/字节数，与磁盘 stat 核对；
  2. **随机抽 8 片实算 sha256 抽查**（不全库重哈希——全库 434G 单线程 ~40min，
     是发布从 20 分钟劣化到 1 小时的主因）；
  3. 拼接各片 truth（`step_offsets` 按 step_base 重定基 + 补收尾边界——
     两个历史 bug，均有测试覆盖）；
  4. 写 `public/`（labels、评测配置）与 `private/`（truth、oracle、final_shards.json）、
     `MANIFEST.json`，软链 `shard_XXX` 进 release 树。
- 拼接/软链等结构改动**必须先有 fixture 测试**（现有 10 个测试就是为
  这类 bug 立的）。
- 发布失败 = 门禁拒发，release 树零副作用；修复后直接重跑本阶段。

## 收 ACCEPT（独立、可重跑、不阻塞）

脚本：`world_generator/condor/run_validate_only_v21.sh <release-dir> [<output-dir>]`

- 物理硬校验只读私有 truth（秒级）：能量守恒、淬灭、（IBD 还有湮灭）。
- 波形门禁 + 6 张核心图：抽 32 事例。
- **内存纪律：worker 只有 4GB**。读取器（plot/validate 共用的
  `ShardWaveforms`）按需打开单片 memmap，构造时全量校验各片索引与
  payload 字节数但不映射波形。任何新读取代码都必须保持这个形态。
- REJECTED 只打回簿记/题面问题；切片树不动，修复后重跑本阶段即可。

## 清 CLEANUP

- 只在 ACCEPTED 之后。
- 先列清单（路径 + 大小 + 用途）给负责人确认，再删。
- 必查：lustre quota 回落有滞后，删完别急着开下一轮大写入。

---

## 历史事故索引（为什么有上面这些规矩）

| 事故 | 根因 | 规矩来源 |
|---|---|---|
| 160 片 ENOSPC 暴毙 | dev 未截断 3× 超盘 | DEV_EVENTS + 配额预留 |
| manifest 谎报成品 | 跳过逻辑只看 index 存在 | `_finalized()` 字节校验 |
| 27 片 final 0 字节 | ENOSPC 崩溃残留 + 上述跳过 | 全量扫雷 + 确定性重放 |
| 240 片 dev 全缺 | 同期清理/崩溃 | 修复流程 + 真相拼接测试 |
| 验收 ENOMEM | 读取器全开 240 片 memmap（336GB 虚拟） | lazy 单片打开（4GB 纪律） |
| rsync 只拷了空目录 | `--files-from` 隐含 -d | 显式 `-r` |
| 作业秒挂 | 漏 export JRB_REPO_ROOT | 2 分钟开工确认 |
| EDQUOT 八路齐挂 | lustre 配额账本滞后回冲 | 写前查 quota + 15% 余量 |
| truth 拼接错位/断尾 | step_offsets 未重定基/未补边界 | fixture 测试覆盖 |
| 改名打断在跑脚本 | finisher 按旧文件名调用 127 | 改名前查引用、挑时机 |
