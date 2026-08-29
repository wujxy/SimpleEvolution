# omilrec_sci_opt — scientist 入世模式跑 OMILREC v1.0.0 优化任务

生产算法优化基准 + 入世 scientist（PI + 席位协作者）：对 JUNO 宏观似然
顶点/能量重建算法 **OMILREC v1.0.0** 做单线程提速，冻结四 gate 全 PASS
前提下压低 `SPEED_MS`（基线 813.53 ms/evt @100 事件，冻结于
`repo/baseline/manifest.json`）。

```bash
bash examples/omilrec_sci_opt/launch_singlenode.sh scientist RUN_DIR
```

世界 = 任务 repo（`examples/omilrec_opt/repo`，v1.0.0 @ 8bbf2f5）；
人类专家参考（`examples/omilrec_opt/reference/`）在一切容器视野之外。

## 容器形状（与 JRB launcher 的全部差异）

| 项 | 值 | 机制 |
|---|---|---|
| NODE_IMAGE | `examples/omilrec_opt/apptainer.sif`（almalinux:9 + JUNO 库链） | launcher |
| WORLD_RW | `OMILRECV2/src .scientist .git build InstallArea TEMP` | node_common 嵌套 overlay |
| EXTRA_RO_BINDS | `/cvmfs` + `/data/juno/dingxf/{OMILREC_maps,inputs}`（窄到目录） | node_common；前人 output/ 不可见 |
| SNAPSHOT_SUBDIR | `OMILRECV2/src` | run_scientist |
| S2/S3/S7 | 布局断言与 eval 冒烟覆写 | smoke.sh 参数化 |

冻结面（EROFS）：`tests/ scripts/ baseline/ CMakeLists docs`。写面
（rw overlay）：编辑面 + eval 自己的 `build/ InstallArea/ TEMP/`。

## 两个已排掉的坑（排障记录）

1. **PYTHONPATH 覆盖杀 pytest**：omilrec sif 自带
   `PYTHONPATH=/usr/local/lib/cvmfs_python311_extra`（cvmfs Python 3.11
   的 pytest——eval 的 gate 套件在 JUNO setup 重指 python 后用它）。
   `node_scientist_env` 的 `APPTAINERENV_PYTHONPATH=/opt/scientist` 会
   **替换**镜像值 → 三 gate 齐死于 `No module named pytest`。修法：
   前置合并（`/opt/scientist:$image_path`，xsbench/jrb 镜像无
   PYTHONPATH，合并不改变其行为）。
2. **钩子时序**：`POST_PREPARE_HOOK` 必须在 spec.json 写出与包冻结
   **之后** eval（node_container 绑 /spec.json，早于落地即 FATAL）。
   机制保留（通用一次性准备钩子），本任务现无钩子。

## 冒烟记录

2026-08-29：`runs/singlenode/omilrec-v100-sci-smoke`，SMOKE_ONLY=1
九门全绿；S7 真 eval（从零 build + 四 gate + 10 事件 bench）：
CONTRACT/FCN/CONSISTENCY/SINGLE_THREADED 全 PASS，
SPEED_MS=867.8（10 事件口径，慢于 100 事件基线属正常），EVAL_RESULT=ok。
隔离审计：容器内 `/data/juno/dingxf` 下只有 OMILREC_maps 与 inputs 两个
绑定目录；无 /datafs；/work 内无 reference/。

注意：完整 eval（100 事件）每轮 5-8 分钟；PI 的一次 bash 上限
3600s（spec.budget）。eval 输出结构化 token（退出码可信）。
