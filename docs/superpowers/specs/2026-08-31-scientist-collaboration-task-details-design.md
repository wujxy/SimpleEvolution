# Scientist Observatory：协作者任务详情设计

日期：2026-08-31

## 1. 目标

Scientist Observatory 当前能显示 Scientist 的 step 和原始 wire 记录，但协作者调用只被概括为 `Scientist called executor` 等文本。操作者无法直接看出 Scientist 给协作者布置了什么任务、以什么标准判断完成。

本次增强让 PI 主时间线中的协作者调用直接呈现 Scientist 实际传入的：

- `brief`：任务内容；
- `definition_of_done`：完成标准。

默认时间线仍保持短摘要；用户点击活动详情后查看两个字段的完整内容，并可继续查看原始 wire 记录。

## 2. 范围

### 2.1 包含

- 识别 `executor`、`searcher`、`proposer`、`challenger`、`reviewer` 和 `continue_engagement` 调用；
- 同一 PI 消息中的多个协作者调用分别形成活动；
- 主时间线展示角色和由 `brief` 生成的短摘要；
- 活动详情完整展示该调用实际传入的 `brief` 和 `definition_of_done`；
- 保留活动到原始 wire 记录的引用；
- 同时支持历史索引和实时追加；
- 对缺失或损坏的参数明确降级。

### 2.2 不包含

- 读取或展示 seat 的 `prompt.txt`；
- 修改 Scientist 运行时、wire 协议或 manifest 格式；
- 展示未由 Scientist 直接传入的角色说明、研究总目标或 memory 提示；
- 改造协作者卡片；
- 语义化所有 `bash`、`read_file`、`wait`、`remember` 等低层行为；
- 使用模型生成摘要。

## 3. 数据来源与真实性边界

唯一语义来源是 `world/.scientist/session/wire.jsonl` 中 assistant 消息的 `tool_calls`。每个协作者调用的 `function.arguments` 包含 Scientist 当时实际提交的参数。当前协议中，所有新协作者调用都传入 `brief`；只有 `executor` 和 `continue_engagement` 另外传入 `definition_of_done`。界面只展示实际存在的字段。

投影层只读取以下白名单字段：

```text
brief
definition_of_done
```

`function.arguments` 兼容 JSON 字符串和已经解码的对象。参数无法解析、字段不存在或类型不正确时，界面不得从 `prompt.txt`、manifest 或自由文本推测缺失内容。

原始 wire 详情继续通过现有不透明 `detail_ref` 读取。浏览器不能提交文件路径，服务也不新增任意文件读取能力。

## 4. 投影模型

`RunReader` 保持不变，继续按稳定字节偏移读取 wire，并为原始记录建立详情索引。

`RunProjector` 对一条 wire 记录中的每个协作者 tool call 分别生成事件。最小事件结构为：

```json
{
  "id": "wire:348123:call_abc",
  "kind": "collaboration_task",
  "role": "executor",
  "summary": "派出 Executor：测量 external-vertex 优化收益",
  "task": {
    "brief": "完整任务内容",
    "definition_of_done": "完整完成标准",
    "available": true
  },
  "detail_refs": ["detail:wire:348123"]
}
```

事件 ID 由 wire 记录稳定 ID 和 `tool_call.id` 组合。若上游缺少 call ID，则使用该 tool call 在记录内的固定序号。这样同一消息中的并行调用不会互相覆盖，服务重启后也不会产生重复事件。

摘要只取 `brief` 的首段并执行现有长度限制；完整字段不截断、不执行 Markdown，也不进入摘要推断。没有传入 `definition_of_done` 的角色不显示“完成标准”区块。

## 5. 页面交互

主时间线活动显示：

```text
派出 Executor
测量跳过 external-vertex reconstruction 后的真实性能收益
[查看活动详情]
```

点击“查看活动详情”后，现有右侧详情区显示：

```text
Executor 任务

任务
<完整 brief>

完成标准
<完整 definition_of_done>

[查看原始记录]
```

“查看原始记录”切换到现有原始 JSON 视图。返回语义详情不触发新的文件读取。

所有运行内容通过 DOM `textContent` 渲染。任务文本中的 HTML、脚本或 Markdown 均作为普通文本显示。

## 6. 降级与错误处理

- tool 名称受支持但 arguments 无法解析：创建派工活动，摘要为“派出 `<Role>`”，详情显示“任务详情不可解析”；
- `brief` 缺失：不从 `definition_of_done` 或其他来源生成任务描述；
- 按协议不接收 `definition_of_done` 的角色：只展示 `brief`，不渲染完成标准区块；
- Executor 或 continuation 的 `definition_of_done` 缺失：完整展示可用的 `brief`，并标记“完成标准不可用”；
- 单条消息含多个 tool call：逐项处理，某一项损坏不影响其他项；
- 非协作者 tool call：维持当前行为，不进入本次结构化任务投影；
- 原始详情已不可读取：语义详情仍可展示，原始区域沿用现有错误提示。

## 7. 测试

### 7.1 投影测试

- 单个 Executor 调用生成一个 `collaboration_task`；
- 同一 wire 消息中的 Executor 和 Searcher 生成两个稳定事件；
- JSON 字符串与对象形式的 arguments 得到相同投影；
- `brief` 和 `definition_of_done` 完整保留，摘要按上限缩短；
- 缺失、错误类型和损坏 JSON 按约定降级；
- 历史重放和实时追加产生相同事件 ID 与内容；
- 非协作者工具保持现有投影行为。

### 7.2 服务与前端测试

- snapshot 中包含语义任务详情和原始 `detail_ref`；
- 页面提供“查看活动详情”和“查看原始记录”的切换；
- 两个任务字段仅作为文本渲染；
- 恶意 HTML 样本不会成为可执行 DOM；
- 现有原始详情路径约束和响应上限保持有效。

## 8. 验收标准

使用现有 `omilrec-v100-r3-scientist` 历史 run 启动 Observatory 后，PI 派出 Executor 的活动必须直接显示可辨认的任务摘要。点击活动详情必须看到该调用中完整的 `brief` 与 `definition_of_done`；点击原始记录仍能查看对应 wire JSON。服务不得读取 `prompt.txt`，不得新增写入行为，并且现有 Scientist Observatory 测试全部通过。
