# singlenode — 一个容器，一个世界，一个 agent

脱离 simpleevo 的单 world 测试框架：把一个 evo node 的容器机器（apptainer 沙箱 +
mount 边界）搬出来，跑**单 scientist** 或**单 coding agent** 的 benchmark 臂。
两种模式共享同一套 mount 语义，跨模式可比性从文件系统层就成立。

```
容器可见性 = $RUN_DIR + 冻结模板 + spec + 镜像，别无他物
/work     = 世界副本，ro 基底 + src/ .scientist/ .git/ 三个 rw 覆盖
           （scripts/ benchmarks/ README 是 EROFS —— "只准改 src" 是物理事实）
/repo     = 冻结模板参照（只读）
/scratch  = 自由空间（spec/env 注入点、pycache、claude-config）
```

## scientist 模式

```bash
bash singlenode/run_scientist.sh <RUN_DIR>          # 默认 xsbench，3h
# 结果: <RUN_DIR>/{run.log, snapshots/, world/, spec.json, smoke.log}
# 回放: python scripts/replay_xsbench.py --snapshots <RUN_DIR>/snapshots --out <RUN_DIR>/replay.csv
```

整个 scientist run 在一次 `apptainer exec` 里：CLI 用镜像内 python3.9（纯 stdlib
模型传输），bash 是容器原生 shell，协作者 claude 用镜像内 CLI 同容器同能力。
run.log 中出现 "Read-only file system" 是**正证据**（物理边界在被触碰）。

## coding 模式

```bash
bash singlenode/run_coding.sh <RUN_DIR>             # TASK_FILE 可覆盖
# 结果: <RUN_DIR>/{trace.jsonl, coding.log, snapshots/, world/}
```

coding 臂配方去 simpleevo 化：prompt 从 stdin 进、stream-json 落 trace.jsonl、
整会话单次 apptainer exec、墙钟到点 watchdog 杀进程组。默认任务文件
`specs/xsbench_coding_task.txt`（goal+gates 的容器路径版）。

## 换 benchmark

三个覆盖变量 + smoke 的 S7（bench 命令是 benchmark 特定的）：

```bash
NODE_IMAGE=$REPO/examples/omilrec_opt/apptainer.sif \
NODE_TEMPLATE=$REPO/examples/omilrec_opt/repo \
SPEC_TEMPLATE=<spec.json> \
bash singlenode/run_scientist.sh <RUN_DIR>
```

## 契约与已知偏差

- 凭证：模型 key 注入 spec（chmod 600，不回显）；coding 模式 claude 凭证经
  `APPTAINERENV_*` 进容器。spec 容器内可读 = 接受的信任域。
- `.git`/`.scientist` 是 rw 覆盖（harness 管道：账本/会话/PI 的 commit）——
  科学上有意义的冻结面是 scripts/ benchmarks/ README。
- 容器 /tmp 单次 exec 内持久、结束即逝。
- 嵌套 apptainer：脚本自动 `unset APPTAINER_BIND` + `--userns`（本机 shell
  自身在容器内）。
- smoke S0-S9 fail-closed（S1 scientist 模式专属，S7 bench 特定）；冒烟和
  probe 都在脱离运行之前，坏镜像/挂载/环境不会起 run。
- 前身：`scripts/run_xsbench_3h_container.sh` + `scripts/_container_common.sh`
  是本框架的原型（container-v1 臂仍在用）；singlenode 是收敛后的通用版。
