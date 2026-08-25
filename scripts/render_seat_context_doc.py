"""Render the complete seat-facing context into the review doc.

Every string below is produced by the production assembly functions —
this is exactly what the model sees, not a hand copy. Rerun after any
wording change so the review doc never drifts from the payload.
"""
from __future__ import annotations

import json
from pathlib import Path

from scientist.agent import (
    _BUDGET_NUDGE,
    _COLD_START,
    _IDLE_NUDGE,
    _SUSPEND_PROMPT,
    _TIME_NUDGE,
    build_system_prompt,
)
from scientist.assistant_tools import _CONSULT_PROMPT, _WORK_PROMPT
from scientist.native_tools import NATIVE_TOOLS, render_native_boundaries

SPEC = Path("runs/oneworld-demo-1/spec.json")
OUT = Path("docs/chat/2026.08.25.17.46.seat完整上下文审阅.md")

spec = json.loads(SPEC.read_text(encoding="utf-8"))
system = build_system_prompt(
    spec,
    notebook="…(resume only: the seat's last research notebook, if any)…",
    notes="…(resume only: notes.md, the append-only working log)…",
    roots={"work": "/work", "repo": "/repo", "scratch": "/scratch"},
)

parts: list[str] = []
parts.append("""# Seat 完整上下文审阅（生产装配函数真实渲染）

> 本文每一节都由生产代码生成（`build_system_prompt` / `NATIVE_TOOLS` /
> 常量原文），即 API payload 实际发送的字节。逐词审阅用。
> 本次更新：采纳 GPT 反馈三处（去 sword-bearer、"exactly what the brief
> says"改意图级委托、"anything mechanical"改 execution）。

## 一、System Prompt 全文（第一条 system 消息）
""")

parts.append("```text\n" + system + "\n```\n")

parts.append("## 二、14 个工具 schema 全文（随 payload 发送）\n")
for tool in NATIVE_TOOLS:
    fn = tool["function"]
    parts.append(f"### {fn['name']}\n")
    parts.append("```text\n" + fn["description"] + "\n```\n")
    parts.append("```json\n"
                 + json.dumps(fn["parameters"], ensure_ascii=False, indent=2)
                 + "\n```\n")

parts.append("## 三、开场消息（第一条 user 消息）\n")
parts.append("```text\n" + _COLD_START + "\n```\n")

parts.append("""## 四、飞行中的全部注入文本

### 空转 nudge（连续 3 轮纯文本不行动时）
""")
parts.append("```text\n" + _IDLE_NUDGE + "\n```\n")
parts.append("### 预算 nudge（步数过半）\n")
parts.append("```text\n" + _BUDGET_NUDGE + "\n```\n")
parts.append("### 时间 nudge（墙钟过 80%）\n")
parts.append("```text\n" + _TIME_NUDGE + "\n```\n")
parts.append("""### work 回执（工具观测，立即返回）
```json
{"ok": true, "call_id": "work-ep-001", "status": "running",
 "mode": "continue", "outstanding_jobs": ["work-ep-001"],
 "note": "your assistant took the brief and works on its own; keep
 reading and thinking — its report arrives as its own message when the
 job is done (jobs not yet reported are still running; wait for one
 with wait)"}
```

### wait 观测（两态，诚实返回）
```json
{"ok": true, "landed": 1,
 "note": "its report arrives as its own message before your next thought"}
```
```json
{"ok": true, "timeout": true,
 "note": "no report landed yet — dispatched jobs are still running"}
```

### 助手报告（作为独立 user 消息送达）
```text
[your assistant finished | work-ep-001]
diff_summary: …(≤300 词)
self_report: …(≤300 词)
metrics: {"self_measured": "…"}
(its own report, not a verdict — verify what matters)
```
""")
parts.append("### 挂起提示（世界暂停时最后一条消息）\n")
parts.append("```text\n" + _SUSPEND_PROMPT + "\n```\n")

parts.append("""## 五、助手侧提示词（seat 看不到，决定关系质量）

### _CONSULT_PROMPT（consult 发给 Claude Code 的原文）
""")
parts.append("```text\n" + _CONSULT_PROMPT + "\n```\n")
parts.append("### _WORK_PROMPT（work 发给 Claude Code 的原文）\n")
parts.append("```text\n" + _WORK_PROMPT + "\n```\n")

OUT.write_text("\n".join(parts), encoding="utf-8")
print(f"{OUT}: {OUT.stat().st_size} chars, {len(NATIVE_TOOLS)} tools")
