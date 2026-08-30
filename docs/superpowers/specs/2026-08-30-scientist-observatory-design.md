# Scientist Observatory：单 Run 只读监视器设计

日期：2026-08-30

## 1. 背景与目标

当前 Scientist 是研究团队的 PI：它在长程运行中形成判断、调用本地工具，并行派出 Searcher、Proposer、Executor、Challenger 和 Reviewer。运行事实已经持久化，但分散在 `run.log`、PI wire、协作者目录和研究账本中。操作者通常只能看到极简日志，无法直接回答：

- Scientist 当前在做什么，为什么停在这里；
- 哪些协作者正在工作，最近做到了哪一步；
- 一项研究判断如何形成，报告是否已被 PI 收取；
- 运行是正常安静、连接中断后恢复，还是已经结束。

首版建设一个 **Scientist Observatory**：启动时指定一个 `RUN_DIR`，在网页中只读观察这一场运行。它解决实时可见性，不承担启动、暂停、终止、发消息、多 run 管理或用户管理。

未来可以在它之上建设 SimpleEvolution 的运行入口，但未来需求不进入本次实现。

## 2. 已确认的产品边界

### 2.1 包含

- 启动时指定一个 run；
- 展示 run 状态、目标、预算、最近可观测活动和用量；
- 以 Scientist 时间线为主线展示工具调用、判断修订、派工、等待、报告收取、崩溃和恢复；
- 展示每个协作者的正式状态、活跃度、任务和最终报告；
- 默认展示确定性的语义化活动摘要；
- 按需读取并展开摘要对应的原始事件；
- 实时增量更新；
- 支持历史 run 回放；
- 对损坏、缺失或不可可靠推导的数据明确降级。

### 2.2 不包含

- 启动、暂停、恢复、终止 Scientist；
- 向 Scientist 或协作者发送消息；
- 修改配置或研究文件；
- 扫描和管理多个 run；
- 数据库、用户系统、鉴权系统；
- DAG 编辑器或研究流程编排；
- React/Vite 或其他 npm 构建链；
- 用另一个语言模型生成监视摘要。

## 3. 真实样本约束

设计以 `runs/singlenode/omilrec-v100-r2-scientist` 为首个验收样本。检查时该 run 呈现了首版必须正确表达的情况：

- 模型 TLS/SSL 连接中断；
- supervisor 保存失败并从 `wire.jsonl` 恢复同一研究进程；
- 恢复后 step 编号重新开始；
- Executor 和 Searcher 并行工作；
- Searcher 已完成并被 PI 收取；
- Executor 的 `raw.txt` 已超过 14 MB，仍在持续增长；
- PI 在等待 Executor 的 profiling 结果；
- run 尚无当前 `conclusion.json`。

因此，Observatory 不能退化成 `run.log` 的网页 tail，也不能假设 step 全局单调、PID 可从宿主机验证，或将长时间无 PI 消息等同于停止。

## 4. 技术路径

采用 **Python 标准库只读服务 + 原生 HTML/CSS/JavaScript + Server-Sent Events（SSE）**。

选择理由：

- 仓库当前是 Python/setuptools 项目，没有现成 Node 前端栈；
- 单向实时更新与只读监视天然适合 SSE；
- 不增加数据库和双构建体系；
- 后端的文件读取、状态投影和前端渲染边界清楚，未来可单独替换前端。

不采用 FastAPI + React/Vite：它对未来运行平台有价值，但对单 run 只读 MVP 引入了不必要的依赖和构建复杂度。

不采用纯静态页面：浏览器无法可靠、安全地增量读取远程服务器上的任意 run 文件。

## 5. 启动与部署

建议入口：

```bash
python -m scientist.ui \
  --run-dir runs/singlenode/omilrec-v100-r2-scientist \
  --host 127.0.0.1 \
  --port 8765
```

默认只监听 `127.0.0.1`。远程使用通过 SSH 端口转发：

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

浏览器访问 `http://127.0.0.1:8765`。首版没有鉴权，不建议直接绑定公网地址。

## 6. 输入数据

服务只读取启动时指定的目录：

```text
RUN_DIR/
├── run.log
├── snapshot.log
├── spec.json
└── world/.scientist/
    ├── session/wire.jsonl
    ├── assistant/<collaborator-id>/
    │   ├── manifest.json
    │   ├── raw.txt
    │   ├── proc.pid
    │   ├── digest.json
    │   └── read.marker
    ├── research_state.jsonl
    ├── research_memory.jsonl
    ├── assistant_calls.jsonl
    ├── usage.jsonl
    └── conclusion*.json
```

`spec.json` 不直接返回。服务只提取目标、episode、预算等白名单字段；`model.api_key`、`assistant.env` 和其他未列入白名单的内容永不进入响应。

## 7. 内部架构

```text
运行文件
   │
   ▼
RunReader
   │  按字节偏移读取、处理追加和残缺尾行
   ▼
RunProjector
   │  归一化事件、生成摘要、投影当前状态
   ▼
ReadOnlyServer
   │  HTTP 初始快照 + SSE 增量
   ▼
原生 Web UI
```

### 7.1 RunReader

RunReader 只负责文件事实，不理解研究语义：

- 校验并固定 `RUN_DIR` 和允许读取的内部文件；
- 按文件保存最后读取的字节偏移；
- 流式读取 JSONL，不整体载入大文件；
- 缓存尚未写完的尾行，等后续字节补齐；
- 为每条原始记录产生稳定来源标识；
- 检测文件截断或替换并请求该来源重建；
- 按安全引用读取详情片段；
- 对单次详情响应执行大小上限。

### 7.2 RunProjector

RunProjector 是无网络、可确定性测试的投影层：

- 将 wire、run log、manifest、raw、digest、research state、memory、usage 和 conclusion 归一化为事件；
- 将事实事件投影成 run、Scientist 和 seat 当前状态；
- 将原始协作者事件压缩成可追溯的活动段；
- 保留摘要到原始记录的引用；
- 对无法解析或无法可靠判断的信息产生 observer warning；
- 相同输入必须得到相同事件 ID、顺序、摘要和状态。

### 7.3 ReadOnlyServer

ReadOnlyServer 只组织静态资源和只读接口：

- 提供初始快照；
- 提供增量事件补取；
- 提供受限的原始详情读取；
- 通过 SSE 推送新增事件和状态变化；
- 浏览器断线后按最后事件 ID 补发；
- 后台索引已有大 transcript，索引期间页面保持可用。

### 7.4 Web UI

前端只渲染后端提供的事件和状态，不自行推断 run 语义。所有来自运行文件的字符串使用 DOM `textContent`，不得作为 HTML 注入。

## 8. 统一事件模型

最小事件结构：

```json
{
  "id": "wire:348123",
  "source": "scientist",
  "kind": "tool_call",
  "occurred_at": null,
  "sequence": 57,
  "summary": "读取 Calculate_EVLikelihood 实现",
  "detail_refs": ["detail:wire:348123"],
  "data": {}
}
```

- `id` 基于逻辑来源和记录起始字节偏移，服务重启后保持稳定；
- `occurred_at` 只有在来源提供可靠时间时才填写；
- `sequence` 表示可靠顺序，不冒充墙钟时间；
- `detail_refs` 是服务生成的不透明引用，不是客户端提供的路径；
- 大段原始文本不进入首屏快照。

主时间线只合并有可靠锚点的事件：`run.log` 中的 attempt/step、manifest 的启动时间、digest 的结束时间和 conclusion。没有跨来源可靠时间的 seat raw 活动保留在各自 seat 的摘要流中，不强行插入 PI 的全局时间线。相同时间的事件使用固定来源优先级和来源内序号排序，保证服务重启后的顺序稳定。

当前 `wire.jsonl` 没有逐条时间戳。首版从 `run.log` 获取 step、失败、恢复、派工和等待的时间，从 manifest/digest 获取协作者开始和结束时间。只有顺序、没有可靠时间的 wire 细项不显示虚构时间。文件修改时间只用于“最近仍有输出”的活跃度提示。

## 9. 语义化活动摘要

首版不调用语言模型。摘要由稳定规则生成：

| 原始事件 | 摘要内容 |
|---|---|
| Read / Grep / Glob | 检查的文件、符号或查询 |
| Edit / Write | 修改的文件 |
| Bash | 命令目的、完成状态、可可靠提取的指标 |
| tool progress | 命令仍在执行及最近进展 |
| assistant 短文本 | 协作者自己声明的当前意图 |
| digest | 最终结论、证据、不确定性和后续建议 |

相邻事件只在属于同一工具调用或同一条明确任务链时合并。一个活动段保存其全部原始 `detail_refs`，例如：

```text
添加临时 profiling 计数器
修改 OMILRECV2.cc，随后重新编译成功
证据：3 个原始事件
```

摘要不声称原始记录没有支持的因果关系。可可靠解析的 benchmark 指标可以显示；自由文本中的猜测不升级为事实。

## 10. 状态语义

### 10.1 协作者正式状态

| 文件事实 | 正式状态 |
|---|---|
| 有 manifest、无 digest | 已启动 |
| `digest.status=done` | 已完成 |
| `timeout-salvaged` | 超时回收 |
| `crash-salvaged` | 崩溃回收 |
| `failed` | 失败 |
| 存在 `read.marker` | 追加“Scientist 已收取” |

对“已启动、无 digest”的 seat，仅附加活跃度，不宣称宿主机进程存活：

```text
已启动 · 8 秒前仍有输出
已启动 · 12 分钟无新输出
已启动 · 已超过 time box，等待运行时回收
```

`proc.pid` 是运行时证据之一，但可能属于容器 PID 命名空间，不能单独决定状态。

### 10.2 Run 正式状态

| 文件事实 | 正式状态 |
|---|---|
| 无当前 `conclusion.json` | 未结论 |
| outcome 为 deliver | 已交付 |
| outcome 为 abstain | 已放弃 |
| outcome 为 cut_off | 被截断 |
| outcome 为 crashed | 已崩溃 |

没有结论但长期无文件变化时，只显示“未结论 · N 分钟无可观测活动”，不显示“已停止”。历史的 `conclusion.*.crashed.json` 作为 attempt 中断事件展示，不覆盖当前 run 的正式状态。

## 11. 页面结构与交互

首屏采用“Scientist 时间线为主、协作者席位为辅”：

```text
┌ Run 状态、目标、预算、最近活动 ──────────────────────────────┐
├ 当前状态：Scientist 正在做什么、为什么在等待 ────────────────┤
├ Scientist 主时间线 ─────────────────┬ 协作者席位 ────────────┤
│ 失败、恢复、判断、派工、等待、收取报告 │ 角色、任务、状态、活跃度 │
├─────────────────────────────────────┴────────────────────────┤
│ 选中对象：语义化活动摘要 / 按需展开的原始详情                 │
└──────────────────────────────────────────────────────────────┘
```

交互规则：

- 点击时间线事件，在详情区查看完整工具参数和结果；
- 点击协作者，在详情区查看该 seat 的活动摘要；
- 点击摘要，按引用读取对应原始事件；
- 默认跟随最新活动；用户向上滚动后暂停自动跟随；
- 用户阅读旧详情时，新事件只增加提示，不抢走当前选择；
- 后台仍在索引历史 transcript 时，先显示 manifest 状态和“正在整理历史活动”。

## 12. API

首版只提供：

```text
GET /api/snapshot
GET /api/events?after=<event-id>
GET /api/details/<detail-id>
GET /api/stream
```

SSE 事件类型：

```text
event_added
seat_updated
run_updated
observer_warning
heartbeat
```

没有 POST、PUT、PATCH、DELETE，也没有执行命令的接口。

## 13. 实时与大文件处理

- 首次请求立即返回已知元数据、PI 时间线、seat manifest/digest 和已完成索引的摘要；
- 后台逐个流式索引已有 transcript，摘要通过 SSE 逐步补齐；
- 每个追加文件保存内存中的读取偏移；
- 不完整 JSONL 尾行不报错，等待下次增长；
- 原始事件只保留轻量索引，摘要和状态保存在内存；
- 详情按偏移读取，超过响应上限时明确标记截断并保留来源说明；
- 正常文件系统条件下以不超过两秒的轮询周期发现新增记录；
- 浏览器重连携带最后事件 ID；服务补发缺失事件；
- 服务重启后重新扫描，稳定 ID 防止前端重复显示。

首版不落盘索引。极长历史 run 的重启扫描成本是已接受的 MVP 权衡；若真实使用证明必要，再单独设计可删除的派生缓存，不能污染 `.scientist` 的事实记录。

## 14. 错误处理

| 情况 | 行为 |
|---|---|
| 损坏的完整 JSON 行 | 跳过并产生 observer warning |
| 正在写入的残缺尾行 | 暂存等待，不算错误 |
| 文件被截断或替换 | 重建该来源并产生 warning |
| 原始详情文件消失 | 保留时间线事件，详情显示证据不可用 |
| SSE 断开 | 显示监视连接断开并自动重连，不改变 run 状态 |
| RUN_DIR 不存在或不可读 | 启动失败，指出确切问题 |
| 某个可选记录文件尚不存在 | 使用空状态继续监视其出现 |

Observatory 自身的故障不得影响 Scientist。观察服务不持有运行进程，不发送信号，也不参与 supervisor 恢复。

## 15. 安全与只读保证

- 服务绝不写入 `RUN_DIR`；
- API 不接受任意路径；
- run 根路径在启动时解析并固定；
- 详情引用只能解析到已索引、允许的来源记录；
- `spec.json` 使用输出白名单；
- 页面对运行内容进行文本转义；
- 默认仅监听 loopback；
- 不读取 `.env`、用户凭据文件或 run 外部路径；
- 前端静态文件属于应用代码，不写入被观察 run。

## 16. 代码边界

```text
scientist/ui/
├── __init__.py
├── __main__.py       # 支持 python -m scientist.ui
├── reader.py
├── projector.py
├── server.py
└── static/
    ├── index.html
    ├── app.js
    └── style.css

tests/scientist/ui/
├── test_reader.py
├── test_projector.py
└── test_server.py
```

- `reader.py`：文件、偏移、增量、详情和路径安全；
- `projector.py`：解析、摘要、状态和稳定事件；
- `server.py`：CLI、HTTP、SSE 和静态资源；
- 前端：展示和本地交互，不复制后端状态规则。

不进行与 Observatory 无关的 Scientist 运行时重构。

## 17. 测试

自动测试覆盖：

- 空 run、运行中 run、已结束 run；
- TLS 类失败后恢复，step 在新 attempt 中重新编号；
- 两个 seat 并行，一个完成、一个仍在输出；
- `manifest → raw → digest → read.marker` 生命周期；
- done、failed、timeout-salvaged、crash-salvaged；
- JSONL 尾行分两次写入；
- 多 MB raw 的流式索引；
- 文件截断或替换后的来源重建；
- 服务重启后事件 ID 稳定；
- 详情偏移、响应上限和缺失来源；
- 路径穿越被拒绝；
- API 响应不包含模型密钥、token 或 assistant env；
- SSE 断线补发不重复、不遗漏；
- Read、Edit、Bash、tool progress 和 digest 的确定性摘要；
- 静态页面入口和只读 API 的集成冒烟。

测试使用临时目录构造最小 run，不依赖或修改正在运行的真实 run。当前 omilrec run 用于最终手工验收。

## 18. 验收标准

1. 对 `omilrec-v100-r2-scientist` 正确显示 attempt 崩溃与恢复、PI 时间线、Searcher 已收取和 Executor 活动摘要。
2. 新记录在正常文件系统条件下两秒内出现在已连接页面。
3. Observatory 或浏览器退出不影响 Scientist。
4. 观察期间 `RUN_DIR` 的内容和时间戳不因 Observatory 改变。
5. 首屏不传输完整 raw；只有展开详情时读取对应片段。
6. 无敏感配置泄漏，无任意路径读取，无日志 HTML 注入。
7. 不增加数据库、前端框架、npm 工程或控制接口。
8. 启动时只观察一个明确指定的 run。

## 19. 后续演进边界

若首版证明有效，后续可以独立设计：多 run 列表、派生索引缓存、运行创建与控制、身份认证、远程部署和更丰富的研究可视化。它们必须通过新的规格进入，不扩张本次只读服务的职责。
