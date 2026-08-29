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

## 已知暴露面与事后审计（2026-08-30 定案：继续跑，记录在案）

`/cvmfs` 全量绑定的既定事实与判读：

- **优化目标 OMILRECV2 不在 release 里**——专家优化版只存在于 /datafs
  （容器外）与 `examples/omilrec_opt/reference/`（从未进容器），答案未漏；
- release 摊着**旧代前实现** `junosw/Reconstruction/OMILREC/`（同源
  QMLE/QTMLE 上一代，源码全量）与 `InstallArea/include/OMILREC/`。
  发射时两臂均未读过；用户裁定**不重启**：旧代属公开工具链（人类
  专家同样可用），读没读可事后审计；
- **事后审计方法**（run 终局时执行）：
  `grep -c "Reconstruction/OMILREC" <run>/world/.scientist/session/wire.jsonl`
  （scientist 臂）与 `grep -c "Reconstruction/OMILREC" <run>/trace.jsonl`
  （coding 臂）——非零即读过旧代源码，成绩需打备注；
- 若未来要根治：node_common 加 `EXTRA_MASK_BINDS`（空目录覆写）遮蔽
  `junosw/Reconstruction/OMILREC` 与 `InstallArea/include/OMILREC`；
  运行链已核实只经 Tutorial 包裹加载 CalibSvc，遮蔽不破 eval（S7 兜底）。

## 冒烟记录

2026-08-29：`runs/singlenode/omilrec-v100-sci-smoke`，SMOKE_ONLY=1
九门全绿；S7 真 eval（从零 build + 四 gate + 10 事件 bench）：
CONTRACT/FCN/CONSISTENCY/SINGLE_THREADED 全 PASS，
SPEED_MS=867.8（10 事件口径，慢于 100 事件基线属正常），EVAL_RESULT=ok。
隔离审计：容器内 `/data/juno/dingxf` 下只有 OMILREC_maps 与 inputs 两个
绑定目录；无 /datafs；/work 内无 reference/。

注意：完整 eval（100 事件）每轮 5-8 分钟；PI 的一次 bash 上限
3600s（spec.budget）。eval 输出结构化 token（退出码可信）。
