# Scientist Multi-Agent Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Scientist 增量演进为由 SCI 调度、多个长期 Executor 并行产出、认知席位异步提供变异、统一事件流共享进展、Candidate 串行验证并由 SCI 接纳的研究团队。

**Architecture:** 保留现有 `InWorldAssistant` 的进程、会话、回收和世界复制能力，在其下方增加且只增加五个正交基础原语：`Seat`、`Workspace`、`Event`、`Candidate`、`Evaluation`。SCI 只通过一个组合根协调这些原语；席位间信息统一写入 Lab Event Stream，Executor 在独立工作区中工作，正式成果以不可变 Candidate 进入单通道 Evaluator，SCI 只负责资源、方向和 accepted head，不直接合并代码。

**Tech Stack:** Python 3.9+、标准库（`dataclasses`、`json`、`fcntl`、`subprocess`、`pathlib`、`threading`）、Git CLI、现有 Claude CLI 适配层、pytest。

## Global Constraints

- 只修改 `scientist/`、`tests/scientist/`、`examples/omilrec_sci_opt/spec.json` 和本方案明确列出的文档；不修改 SimpleEvolution 主体、`.env`、密钥或生产配置。
- 第一版固定为五个基础原语：`Seat`、`Workspace`、`Event`、`Candidate`、`Evaluation`；不得为 `finding`、`dissent`、`review` 等语义另建队列、信道或 runtime handler。
- SCI 独占资源分配、研究 charter 与 accepted head；Executor 独占正式 Candidate 提交；Evaluator 独占权威 gate/score；每个 Workspace 同时只有一个 owner。
- Executor 默认使用独立工作区并可长期 `active`、`parked`、`retired`；认知席位默认临时、异步，Reviewer 不构成强制审批关卡。
- 所有跨席位信息只通过一个持久化 Lab Event Stream；`public` 与定向 audience 使用相同存储、相同 cursor 机制。
- accepted head 更新后只广播事实；Executor 自主选择继续、同步或重启，runtime 禁止自动 rebase。
- Candidate 核心内容不可变；任何成果组合都由指定 Executor 完成，并以新 Candidate 重新进入 Evaluation。
- 权威 Evaluation 同时最多运行一个；工具故障与研究失败必须是不同状态。
- 认知席位实验必须从明确的稳定 revision 创建一次性 Workspace，不复制其他席位的 dirty live tree，也不能直接提交正式 Candidate。
- SCI 优化的是单位 wall-clock 的有效研究进展，不是活跃 Agent 数量；并行独立学习机会，串行真实依赖。
- research line 由 Seat charter 和五原语状态表达；portfolio 是派生视图，不新增 `ResearchLine` 存储、依赖图引擎或自动调度器。
- 给同一方向增加火力时创建独立 Seat/Workspace/子路线；禁止两个 Executor 共同写一个 Workspace。
- SCI 可以只读调查和形成假设，但不直接修改 accepted world 或提交 Candidate；生产性实验和成果吸收必须分配给 Executor。
- 保留工作树中既有用户修改。每次提交只 `git add` 本任务列出的精确文件，禁止 `git add -A`。
- 每项功能遵循 TDD；先运行目标测试观察预期失败，再写最小实现，再运行目标测试与相关回归测试。

---

## File Structure

### 新建文件

- `scientist/lab_events.py`：唯一的跨席位事件存储，负责 append、按 audience 读取、cursor 确认和并发锁。
- `scientist/workspaces.py`：独立工作区所有权、稳定 revision fork、checkpoint、同步状态与显式同步。
- `scientist/seats.py`：稳定 seat identity、生命周期、资源上限和重启后恢复元数据。
- `scientist/candidates.py`：不可变 Candidate、状态转换、accepted head 的 compare-and-swap 更新。
- `scientist/evaluation.py`：单通道权威评测队列、子进程执行、结果分类与崩溃恢复。
- `scientist/lab.py`：五个原语的唯一组合根；提供 SCI board、后台推进和接纳事务。
- `scientist/lab_cli.py`：席位通过现有 Bash 调用的薄 CLI；只暴露事件、checkpoint、Candidate 与同步原语。
- `tests/scientist/test_lab_events.py`：Event 的并发、audience、cursor 测试。
- `tests/scientist/test_workspaces.py`：稳定 fork、所有权、dirty 拒绝、显式同步测试。
- `tests/scientist/test_seats.py`：稳定身份、状态机、容量与恢复测试。
- `tests/scientist/test_candidates.py`：不可变内容、合法转换与 accepted CAS 测试。
- `tests/scientist/test_evaluation.py`：串行性、成功/失败/工具故障与恢复测试。
- `tests/scientist/test_lab.py`：Candidate→Evaluation→接纳→广播闭环测试。
- `tests/scientist/test_lab_cli.py`：角色权限和席位侧 CLI 合约测试。
- `tests/scientist/test_portfolio_integration.py`：多 Executor、异步认知席位、广播与重启端到端测试。

### 修改文件

- `scientist/mkexp.py`：支持从显式 revision 创建稳定副本并记录来源。
- `scientist/assistant_tools.py`：接入稳定 Seat/Workspace，默认隔离 Executor，注入事件环境，支持 resume/park/retire 与活进程再接管。
- `scientist/collaboration.py`：用实验室制度、事件检查点、角色边界替换旧的同步汇报约定。
- `scientist/native_tools.py`：保留角色启动入口，加入最小 SCI 控制面，删除 Reviewer 专用审批语义。
- `scientist/agent.py`：接入 `LabRuntime.tick()`/board，所有席位异步启动，移除 Reviewer listen gate，修正终止生命周期。
- `scientist/cli.py`：解析容量和 Evaluation 配置，创建 `LabRuntime` 并注入 session。
- `scientist/ui/reader.py`：读取 events、seats、candidates、evaluations、accepted head。
- `scientist/ui/projector.py`：把五原语投影成实验室视图，不解释自定义事件 label。
- `examples/omilrec_sci_opt/spec.json`：增加最小容量和权威评测命令示例。
- `tests/scientist/test_oneworld.py`、`test_async_runtime.py`、`test_collaboration.py`、`test_model_native_tools.py`、`ui/test_reader.py`、`ui/test_projector.py`：更新现有合约与回归测试。

### 不新增的结构

- 不新增 role-specific mailbox、Reviewer queue、merge service、proposal service 或独立广播信道。
- 不重写 `LocalLedger`；它继续保存研究记忆和兼容记录，跨席位协作统一走 `LabEventStream`。
- 不引入数据库、消息中间件、GitPython 或新的 agent runtime。

## Preflight

- [ ] 确认分支和脏工作树，只记录现状，不清理用户修改。

```bash
git branch --show-current
git status --short
```

Expected: branch 为 `scientist-multi-agent-portfolio`；脏文件保持原样。

- [ ] 运行 Scientist 基线测试并把失败项记入执行日志；后续只把“新增失败”视为本方案回归。

```bash
python -m pytest tests/scientist -q
```

Expected: 输出当前基线；若已有失败，保存完整 node id 和错误文本，不在本步骤顺带修复。

---

### Task 1: Unified Lab Event Stream

**Files:**
- Create: `scientist/lab_events.py`
- Create: `tests/scientist/test_lab_events.py`

**Interfaces:**
- Consumes: filesystem directory `state_dir: Path`。
- Produces: `LabEventStream.append(author: str, audience: str | Sequence[str], label: str, payload: Mapping[str, object], evidence_refs: Sequence[str] = (), workspace: str | None = None, revision: str | None = None) -> LabEvent`；`read(seat_id: str, after: int | None = None, limit: int = 100) -> list[LabEvent]`；`acknowledge(seat_id: str, event_id: int) -> None`；`unread_count(seat_id: str) -> int`。实现字段 `label` 对应设计文档中的 `event_type`，它是无 handler 的不透明语义名。

- [ ] **Step 1: Write the failing audience and cursor tests**

```python
# tests/scientist/test_lab_events.py
from concurrent.futures import ThreadPoolExecutor

from scientist.lab_events import LabEventStream


def test_public_and_targeted_events_share_one_ordered_stream(tmp_path):
    stream = LabEventStream(tmp_path)
    public = stream.append("sci", "public", "accepted_revision_advanced", {"head": "abc"})
    private = stream.append("reviewer-1", ["executor-2"], "dissent", {"claim": "x"})

    assert [event.event_id for event in stream.read("executor-2")] == [public.event_id, private.event_id]
    assert [event.event_id for event in stream.read("executor-1")] == [public.event_id]

    stream.acknowledge("executor-2", public.event_id)
    assert [event.event_id for event in stream.read("executor-2")] == [private.event_id]
    assert stream.unread_count("executor-2") == 1


def test_concurrent_appends_have_unique_monotonic_ids(tmp_path):
    stream = LabEventStream(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(lambda number: stream.append("sci", "public", "note", {"n": number}), range(40)))

    ids = sorted(event.event_id for event in events)
    assert ids == list(range(1, 41))
    assert [event.event_id for event in stream.read("executor-1")] == ids
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_lab_events.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.lab_events'`.

- [ ] **Step 3: Implement the one-stream storage and cursor contract**

```python
# scientist/lab_events.py
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


_SEAT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class LabEvent:
    event_id: int
    created_at: float
    author: str
    audience: tuple[str, ...]
    label: str
    payload: dict[str, object]
    evidence_refs: tuple[str, ...]
    workspace: str | None
    revision: str | None


class LabEventStream:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "lab-events"
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / "stream.lock"
        self.cursor_dir = self.root / "cursors"
        self.cursor_dir.mkdir(exist_ok=True)

    def _cursor_path(self, seat_id: str) -> Path:
        if not _SEAT_ID.fullmatch(seat_id):
            raise ValueError(f"invalid seat id: {seat_id}")
        return self.cursor_dir / f"{seat_id}.cursor"

    def _rows(self) -> list[dict[str, object]]:
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line]

    @staticmethod
    def _decode(row: Mapping[str, object]) -> LabEvent:
        return LabEvent(
            event_id=int(row["event_id"]), created_at=float(row["created_at"]), author=str(row["author"]),
            audience=tuple(str(item) for item in row["audience"]), label=str(row["label"]),
            payload=dict(row["payload"]), evidence_refs=tuple(str(item) for item in row["evidence_refs"]),
            workspace=None if row["workspace"] is None else str(row["workspace"]),
            revision=None if row["revision"] is None else str(row["revision"]),
        )

    def append(self, author: str, audience: str | Sequence[str], label: str,
               payload: Mapping[str, object], evidence_refs: Sequence[str] = (),
               workspace: str | None = None, revision: str | None = None) -> LabEvent:
        recipients = (audience,) if isinstance(audience, str) else tuple(audience)
        if not recipients or ("public" in recipients and len(recipients) != 1):
            raise ValueError("audience must be 'public' or one or more seat ids")
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            rows = self._rows()
            event = LabEvent(len(rows) + 1, time.time(), author, recipients, label, dict(payload),
                             tuple(evidence_refs), workspace, revision)
            with self.events_path.open("a", encoding="utf-8") as target:
                target.write(json.dumps(asdict(event), sort_keys=True) + "\n")
                target.flush()
                os.fsync(target.fileno())
            return event

    def read(self, seat_id: str, after: int | None = None, limit: int = 100) -> list[LabEvent]:
        cursor_path = self._cursor_path(seat_id)
        cursor = json.loads(cursor_path.read_text(encoding="utf-8"))["event_id"] if cursor_path.exists() else 0
        floor = cursor if after is None else after
        visible = [self._decode(row) for row in self._rows()
                   if int(row["event_id"]) > floor and (row["audience"] == ["public"] or seat_id in row["audience"])]
        return visible[:limit]

    def acknowledge(self, seat_id: str, event_id: int) -> None:
        path = self._cursor_path(seat_id)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = json.loads(path.read_text(encoding="utf-8"))["event_id"] if path.exists() else 0
            if event_id < current:
                return
            temporary = path.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_text(json.dumps({"event_id": event_id, "acknowledged_at": time.time()}), encoding="utf-8")
            os.replace(temporary, path)

    def unread_count(self, seat_id: str) -> int:
        return len(self.read(seat_id, limit=1_000_000))
```

- [ ] **Step 4: Run focused tests and existing persistence tests**

Run: `python -m pytest tests/scientist/test_lab_events.py tests/scientist/test_persistence_contract.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only Event files**

```bash
git add scientist/lab_events.py tests/scientist/test_lab_events.py
git commit -m "feat(scientist): add unified lab event stream"
```

---

### Task 2: Owned Workspaces and Stable Revision Forks

**Files:**
- Create: `scientist/workspaces.py`
- Modify: `scientist/mkexp.py`
- Create: `tests/scientist/test_workspaces.py`

**Interfaces:**
- Consumes: existing `scientist.mkexp.fork_world(source: Path, dest: Path, revision: str | None = None) -> str`.
- Produces: `WorkspaceRef(owner_id: str, path: Path, base_revision: str)`；`WorkspaceManager.source_for_revision(revision: str) -> Path`；`fork(owner_id: str, revision: str) -> WorkspaceRef`；`checkpoint(owner_id: str) -> str`；`sync_status(owner_id: str, accepted_revision: str) -> Literal["current", "behind", "ahead", "diverged"]`；`sync_facts(owner_id: str, accepted_revision: str) -> dict[str, object]`；`sync_from_accepted(owner_id: str, accepted_revision: str) -> str`。

- [ ] **Step 1: Write failing stable-fork and explicit-sync tests**

```python
# tests/scientist/test_workspaces.py
import json
import subprocess

import pytest

from scientist.workspaces import WorkspaceManager


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "value.txt").write_text("one\n", encoding="utf-8")
    git(path, "add", "value.txt")
    git(path, "commit", "-m", "one")
    return git(path, "rev-parse", "HEAD")


def test_fork_uses_explicit_revision_not_dirty_live_tree(tmp_path):
    live = tmp_path / "live"
    first = make_repo(live)
    (live / "value.txt").write_text("dirty\n", encoding="utf-8")
    manager = WorkspaceManager(live, tmp_path / "seats")

    ref = manager.fork("executor-1", first)

    assert (ref.path / "value.txt").read_text(encoding="utf-8") == "one\n"
    assert ref.base_revision == first
    assert manager.checkpoint("executor-1") == first


def test_sync_refuses_dirty_workspace_and_never_rebases(tmp_path):
    live = tmp_path / "live"
    first = make_repo(live)
    manager = WorkspaceManager(live, tmp_path / "seats")
    ref = manager.fork("executor-1", first)
    (live / "value.txt").write_text("two\n", encoding="utf-8")
    git(live, "add", "value.txt")
    git(live, "commit", "-m", "two")
    accepted = git(live, "rev-parse", "HEAD")
    (ref.path / "local.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="dirty workspace"):
        manager.sync_from_accepted("executor-1", accepted)

    (ref.path / "local.txt").unlink()
    assert manager.sync_from_accepted("executor-1", accepted) == accepted
    assert git(ref.path, "rev-parse", "HEAD") == accepted
    assert "rebase" not in git(ref.path, "reflog", "-1")


def test_conflicting_sync_preserves_live_head_and_executor_commits(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    manager = WorkspaceManager(live, tmp_path / "seats")
    ref = manager.fork("executor-1", base)
    (ref.path / "value.txt").write_text("executor\n", encoding="utf-8")
    git(ref.path, "add", "value.txt")
    git(ref.path, "commit", "-m", "executor")
    executor_head = git(ref.path, "rev-parse", "HEAD")
    (live / "value.txt").write_text("accepted\n", encoding="utf-8")
    git(live, "add", "value.txt")
    git(live, "commit", "-m", "accepted")
    accepted = git(live, "rev-parse", "HEAD")

    with pytest.raises(subprocess.CalledProcessError):
        manager.sync_from_accepted("executor-1", accepted)

    assert git(live, "rev-parse", "HEAD") == accepted
    assert git(ref.path, "rev-parse", "HEAD") == executor_head
    assert "value.txt" in git(ref.path, "diff", "--name-only", "--diff-filter=U")


def test_cognitive_fork_can_use_an_executor_checkpoint_absent_from_live(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    manager = WorkspaceManager(live, tmp_path / "seats")
    executor = manager.fork("executor-1", base)
    (executor.path / "probe.txt").write_text("checkpoint\n", encoding="utf-8")
    git(executor.path, "add", "probe.txt")
    git(executor.path, "commit", "-m", "checkpoint")
    checkpoint = manager.checkpoint("executor-1")

    cognitive = manager.fork("reviewer-1", checkpoint)

    assert (cognitive.path / "probe.txt").read_text(encoding="utf-8") == "checkpoint\n"
    assert git(live, "rev-parse", "HEAD") == base
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_workspaces.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.workspaces'`.

- [ ] **Step 3: Extend `fork_world` to pin tracked files to an explicit commit**

```python
# scientist/mkexp.py — add these helpers, then replace fork_world
def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True,
        text=True, capture_output=True,
    ).stdout.strip()


def _copy_world(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=False)
    source = Path(source).absolute()
    for entry in sorted(source.iterdir()):
        if entry.name == ".scientist":
            continue
        target = dest / entry.name
        if entry.is_dir() and (entry.name == "benchmarks" or _tree_bytes(entry) >= _FORK_SYMLINK_MIN_BYTES):
            target.symlink_to(entry, target_is_directory=True)
        elif entry.is_dir():
            shutil.copytree(entry, target, ignore=shutil.ignore_patterns(".scientist"))
        else:
            shutil.copy2(entry, target)


def fork_world(source: Path, dest: Path, revision: str | None = None) -> str:
    source = source.resolve()
    dest = dest.resolve()
    if dest.exists():
        raise FileExistsError(dest)
    _copy_world(source, dest)
    requested = revision or _git(source, "rev-parse", "HEAD")
    resolved = _git(source, "rev-parse", f"{requested}^{{commit}}")
    if revision is not None:
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--detach", "--force", resolved],
            check=True, text=True, capture_output=True,
        )
    if _git(dest, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError(f"fork is not clean at {resolved}")
    (dest / "EXPERIMENT_BASELINE").write_text(resolved + "\n", encoding="utf-8")
    return resolved
```

Add `parser.add_argument("--revision")` beside the existing source/destination arguments and call `fork_world(args.source, args.dest, args.revision)`. Remove the old duplicate baseline-writing block from `main`; `fork_world` now owns that record.

- [ ] **Step 4: Implement ownership, checkpoint and merge-based sync**

```python
# scientist/workspaces.py
from __future__ import annotations

import fcntl
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from scientist.mkexp import fork_world


SyncStatus = Literal["current", "behind", "ahead", "diverged"]


@dataclass(frozen=True)
class WorkspaceRef:
    owner_id: str
    path: Path
    base_revision: str


class WorkspaceManager:
    def __init__(self, live_world: Path, root: Path):
        self.live_world = Path(live_world).resolve()
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, owner_id: str) -> Path:
        return self.root / owner_id / ".scientist-workspace.json"

    def _run(self, path: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(["git", "-C", str(path), *args], check=check, text=True, capture_output=True)
        return result.stdout.strip()

    def _load(self, owner_id: str) -> WorkspaceRef:
        row = json.loads(self._meta_path(owner_id).read_text(encoding="utf-8"))
        return WorkspaceRef(row["owner_id"], Path(row["path"]), row["base_revision"])

    def fork(self, owner_id: str, revision: str) -> WorkspaceRef:
        dest = self.root / owner_id
        source = self.source_for_revision(revision)
        resolved = fork_world(source, dest, revision)
        ref = WorkspaceRef(owner_id, dest, resolved)
        self._meta_path(owner_id).write_text(json.dumps({**asdict(ref), "path": str(dest)}, sort_keys=True), encoding="utf-8")
        return ref

    def source_for_revision(self, revision: str) -> Path:
        candidates = [self.live_world]
        candidates.extend(path.parent for path in self.root.glob("*/.scientist-workspace.json"))
        for path in candidates:
            result = subprocess.run(["git", "-C", str(path), "cat-file", "-e", f"{revision}^{{commit}}"],
                                    text=True, capture_output=True)
            if result.returncode == 0:
                return path
        raise ValueError(f"revision is not present in a stable workspace: {revision}")

    def checkpoint(self, owner_id: str) -> str:
        ref = self._load(owner_id)
        if self._run(ref.path, "status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError(f"dirty workspace: {owner_id}")
        return self._run(ref.path, "rev-parse", "HEAD")

    def sync_status(self, owner_id: str, accepted_revision: str) -> SyncStatus:
        ref = self._load(owner_id)
        self._run(ref.path, "fetch", str(self.live_world), accepted_revision)
        head = self._run(ref.path, "rev-parse", "HEAD")
        if head == accepted_revision:
            return "current"
        base = self._run(ref.path, "merge-base", head, accepted_revision)
        if base == head:
            return "behind"
        if base == accepted_revision:
            return "ahead"
        return "diverged"

    def sync_facts(self, owner_id: str, accepted_revision: str) -> dict[str, object]:
        ref = self._load(owner_id)
        self._run(ref.path, "fetch", str(self.live_world), accepted_revision)
        head = self._run(ref.path, "rev-parse", "HEAD")
        base = self._run(ref.path, "merge-base", head, accepted_revision)
        local_paths = set(self._run(ref.path, "diff", "--name-only", base, head).splitlines())
        accepted_paths = set(self._run(ref.path, "diff", "--name-only", base, accepted_revision).splitlines())
        return {"my_base": ref.base_revision, "my_head": head,
                "accepted_head": accepted_revision,
                "status": self.sync_status(owner_id, accepted_revision),
                "changed_path_overlap": sorted(local_paths & accepted_paths)}

    def sync_from_accepted(self, owner_id: str, accepted_revision: str) -> str:
        ref = self._load(owner_id)
        if self._run(ref.path, "status", "--porcelain"):
            raise RuntimeError(f"dirty workspace: {owner_id}")
        self._run(ref.path, "fetch", str(self.live_world), accepted_revision)
        subprocess.run(
            ["git", "-C", str(ref.path), "merge", "--no-edit", accepted_revision],
            check=True, text=True, capture_output=True,
        )
        return self._run(ref.path, "rev-parse", "HEAD")
```

- [ ] **Step 5: Run focused and mkexp regression tests**

Run: `python -m pytest tests/scientist/test_workspaces.py tests/scientist/test_oneworld.py -q`

Expected: PASS. If an old test expects dirty current-tree copying, update that assertion to explicit-revision behavior rather than adding a compatibility branch.

- [ ] **Step 6: Commit only Workspace files**

```bash
git add scientist/mkexp.py scientist/workspaces.py tests/scientist/test_workspaces.py tests/scientist/test_oneworld.py
git commit -m "feat(scientist): add owned revision-pinned workspaces"
```

---

### Task 3: Stable Seats, Lifecycle and Capacity

**Files:**
- Create: `scientist/seats.py`
- Create: `tests/scientist/test_seats.py`

**Interfaces:**
- Consumes: `state_dir: Path` and configured `executor_slots: int`, `cognitive_slots: int`.
- Produces: `SeatRecord`；`SeatRegistry.create(role: str, workspace: str | None, charter: str) -> SeatRecord`；`attach_engagement(seat_id: str, engagement_id: str, pid: int | None) -> SeatRecord`；`transition(seat_id: str, target: SeatStatus, reason: str) -> SeatRecord`；`list(role: str | None = None) -> list[SeatRecord]`；`active_counts() -> dict[str, int]`。

- [ ] **Step 1: Write failing capacity and state-machine tests**

```python
# tests/scientist/test_seats.py
import pytest

from scientist.seats import SeatRegistry


def test_executor_identity_survives_engagement_changes(tmp_path):
    registry = SeatRegistry(tmp_path, executor_slots=2, cognitive_slots=1)
    seat = registry.create("executor", "/tmp/world-1", "route A")
    first = registry.attach_engagement(seat.seat_id, "call-1", 101)
    parked = registry.transition(seat.seat_id, "parked", "waiting for evidence")
    resumed = registry.attach_engagement(parked.seat_id, "call-2", 202)

    assert resumed.seat_id == first.seat_id
    assert resumed.engagement_id == "call-2"
    assert resumed.status == "active"


def test_capacity_is_atomic_and_separate_by_seat_class(tmp_path):
    registry = SeatRegistry(tmp_path, executor_slots=1, cognitive_slots=1)
    registry.create("executor", "/tmp/world-1", "route A")
    with pytest.raises(RuntimeError, match="executor capacity exhausted"):
        registry.create("executor", "/tmp/world-2", "route B")

    registry.create("reviewer", None, "audit A")
    with pytest.raises(RuntimeError, match="cognitive capacity exhausted"):
        registry.create("searcher", None, "search B")


def test_retired_seat_cannot_resume(tmp_path):
    registry = SeatRegistry(tmp_path, executor_slots=1, cognitive_slots=1)
    seat = registry.create("executor", "/tmp/world-1", "route A")
    registry.transition(seat.seat_id, "retired", "route abandoned")
    with pytest.raises(ValueError, match="retired"):
        registry.attach_engagement(seat.seat_id, "call-2", 202)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_seats.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.seats'`.

- [ ] **Step 3: Implement the locked registry and explicit transitions**

```python
# scientist/seats.py
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal


SeatStatus = Literal["active", "parked", "retired"]
COGNITIVE_ROLES = frozenset({"searcher", "proposer", "challenger", "reviewer"})


@dataclass(frozen=True)
class SeatRecord:
    seat_id: str
    role: str
    status: SeatStatus
    workspace: str | None
    charter: str
    engagement_id: str | None
    pid: int | None
    reason: str


class SeatRegistry:
    def __init__(self, state_dir: Path, executor_slots: int, cognitive_slots: int):
        if executor_slots < 1 or cognitive_slots < 0:
            raise ValueError("invalid seat capacity")
        self.root = Path(state_dir) / "seats"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / "registry.lock"
        self.executor_slots = executor_slots
        self.cognitive_slots = cognitive_slots

    def _rows(self) -> list[SeatRecord]:
        rows = []
        for path in sorted(self.root.glob("*.json")):
            rows.append(SeatRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return rows

    def _write(self, record: SeatRecord) -> None:
        target = self.root / f"{record.seat_id}.json"
        temporary = target.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(json.dumps(asdict(record), sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)

    def _class(self, role: str) -> str:
        if role == "executor":
            return "executor"
        if role in COGNITIVE_ROLES:
            return "cognitive"
        raise ValueError(f"unknown role: {role}")

    def create(self, role: str, workspace: str | None, charter: str) -> SeatRecord:
        seat_class = self._class(role)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            rows = self._rows()
            active = [row for row in rows if row.status == "active" and self._class(row.role) == seat_class]
            limit = self.executor_slots if seat_class == "executor" else self.cognitive_slots
            if len(active) >= limit:
                raise RuntimeError(f"{seat_class} capacity exhausted")
            number = 1 + max((int(row.seat_id.rsplit("-", 1)[1]) for row in rows if row.seat_id.startswith(role + "-")), default=0)
            record = SeatRecord(f"{role}-{number}", role, "active", workspace, charter, None, None, "created")
            self._write(record)
            return record

    def get(self, seat_id: str) -> SeatRecord:
        return SeatRecord(**json.loads((self.root / f"{seat_id}.json").read_text(encoding="utf-8")))

    def attach_engagement(self, seat_id: str, engagement_id: str, pid: int | None) -> SeatRecord:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self.get(seat_id)
            if current.status == "retired":
                raise ValueError(f"seat is retired: {seat_id}")
            seat_class = self._class(current.role)
            active = [row for row in self._rows() if row.status == "active"
                      and row.seat_id != seat_id and self._class(row.role) == seat_class]
            limit = self.executor_slots if seat_class == "executor" else self.cognitive_slots
            if current.status != "active" and len(active) >= limit:
                raise RuntimeError(f"{seat_class} capacity exhausted")
            updated = replace(current, status="active", engagement_id=engagement_id, pid=pid, reason="engaged")
            self._write(updated)
            return updated

    def transition(self, seat_id: str, target: SeatStatus, reason: str) -> SeatRecord:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self.get(seat_id)
            if current.status == "retired" and target != "retired":
                raise ValueError(f"seat is retired: {seat_id}")
            updated = replace(current, status=target, pid=None if target != "active" else current.pid, reason=reason)
            self._write(updated)
            return updated

    def list(self, role: str | None = None) -> list[SeatRecord]:
        return [row for row in self._rows() if role is None or row.role == role]

    def active_counts(self) -> dict[str, int]:
        rows = [row for row in self._rows() if row.status == "active"]
        return {
            "executor": sum(row.role == "executor" for row in rows),
            "cognitive": sum(row.role in COGNITIVE_ROLES for row in rows),
        }
```

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/scientist/test_seats.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only Seat files**

```bash
git add scientist/seats.py tests/scientist/test_seats.py
git commit -m "feat(scientist): add stable seat lifecycle"
```

---

### Task 4: Immutable Candidates and Accepted Head

**Files:**
- Create: `scientist/candidates.py`
- Create: `tests/scientist/test_candidates.py`

**Interfaces:**
- Consumes: role, owner seat id, workspace path, base revision and candidate revision.
- Produces: `CandidateStore.submit(role: str, owner_id: str, workspace: str, base_revision: str, revision: str, claim: str, evidence_refs: Sequence[str], change_summary: str = "", quick_checks: Sequence[str] = (), risks: Sequence[str] = (), open_questions: Sequence[str] = (), verify_with: Sequence[str] = ()) -> Candidate`；`mark_queued(candidate_id: str) -> CandidateState`；`mark_validating(candidate_id: str, evaluation_id: str) -> CandidateState`；`record_evaluation(candidate_id: str, evaluation_id: str, outcome: str) -> CandidateState`；`reject(candidate_id: str, reason: str) -> CandidateState`；`accept(candidate_id: str, expected_head: str) -> AcceptedHead`；`accepted() -> AcceptedHead`。

- [ ] **Step 1: Write failing immutability, permission and CAS tests**

```python
# tests/scientist/test_candidates.py
import pytest

from scientist.candidates import CandidateStore


def test_only_executor_submits_immutable_candidate(tmp_path):
    store = CandidateStore(tmp_path, initial_head="base")
    candidate = store.submit("executor", "executor-1", "/tmp/w1", "base", "rev-1", "faster route", ["evt-7"])
    with pytest.raises(FileExistsError):
        store.submit("executor", "executor-1", "/tmp/w1", "base", "rev-1", "changed", [])
    with pytest.raises(PermissionError):
        store.submit("reviewer", "reviewer-1", "/tmp/w2", "base", "rev-2", "audit patch", [])
    assert store.get(candidate.candidate_id).claim == "faster route"


def test_accept_requires_passed_evaluation_and_expected_head(tmp_path):
    store = CandidateStore(tmp_path, initial_head="base")
    candidate = store.submit("executor", "executor-1", "/tmp/w1", "base", "rev-1", "claim", [])
    store.mark_queued(candidate.candidate_id)
    store.mark_validating(candidate.candidate_id, "eval-1")
    store.record_evaluation(candidate.candidate_id, "eval-1", "passed")

    with pytest.raises(RuntimeError, match="accepted head changed"):
        store.accept(candidate.candidate_id, expected_head="wrong")

    accepted = store.accept(candidate.candidate_id, expected_head="base")
    assert accepted.revision == "rev-1"
    assert accepted.candidate_id == candidate.candidate_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_candidates.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.candidates'`.

- [ ] **Step 3: Implement immutable core files and mutable state files**

```python
# scientist/candidates.py — public data model and transition table
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Sequence


CandidateStatus = Literal["submitted", "queued", "validating", "passed", "failed", "instrument_failure", "rejected", "accepted", "superseded"]


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    owner_id: str
    workspace: str
    base_revision: str
    revision: str
    claim: str
    evidence_refs: tuple[str, ...]
    change_summary: str
    quick_checks: tuple[str, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]
    verify_with: tuple[str, ...]


@dataclass(frozen=True)
class CandidateState:
    candidate_id: str
    status: CandidateStatus
    evaluation_id: str | None
    reason: str


@dataclass(frozen=True)
class AcceptedHead:
    revision: str
    candidate_id: str | None


class CandidateStore:
    def __init__(self, state_dir: Path, initial_head: str):
        self.root = Path(state_dir) / "candidates"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.root / "store.lock"
        self.accepted_path = self.root / "accepted.json"
        if not self.accepted_path.exists():
            self._atomic(self.accepted_path, asdict(AcceptedHead(initial_head, None)))

    def _atomic(self, path: Path, value: dict[str, object]) -> None:
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def _core(self, candidate_id: str) -> Path:
        return self.root / f"{candidate_id}.core.json"

    def _state(self, candidate_id: str) -> Path:
        return self.root / f"{candidate_id}.state.json"

    def submit(self, role: str, owner_id: str, workspace: str, base_revision: str,
               revision: str, claim: str, evidence_refs: Sequence[str],
               change_summary: str = "", quick_checks: Sequence[str] = (),
               risks: Sequence[str] = (), open_questions: Sequence[str] = (),
               verify_with: Sequence[str] = ()) -> Candidate:
        if role != "executor":
            raise PermissionError("only executor may submit a production candidate")
        candidate_id = f"cand-{revision[:12]}"
        core = Candidate(candidate_id, owner_id, workspace, base_revision, revision, claim,
                         tuple(evidence_refs), change_summary, tuple(quick_checks), tuple(risks),
                         tuple(open_questions), tuple(verify_with))
        path = self._core(candidate_id)
        with path.open("x", encoding="utf-8") as target:
            json.dump(asdict(core), target, sort_keys=True)
        self._atomic(self._state(candidate_id), asdict(CandidateState(candidate_id, "submitted", None, "")))
        return core

    def get(self, candidate_id: str) -> Candidate:
        row = json.loads(self._core(candidate_id).read_text(encoding="utf-8"))
        for field in ("evidence_refs", "quick_checks", "risks", "open_questions", "verify_with"):
            row[field] = tuple(row[field])
        return Candidate(**row)

    def state(self, candidate_id: str) -> CandidateState:
        return CandidateState(**json.loads(self._state(candidate_id).read_text(encoding="utf-8")))

    def _move(self, candidate_id: str, allowed: set[str], target: CandidateStatus,
              evaluation_id: str | None = None, reason: str = "") -> CandidateState:
        current = self.state(candidate_id)
        if current.status not in allowed:
            raise ValueError(f"invalid candidate transition: {current.status} -> {target}")
        updated = replace(current, status=target, evaluation_id=evaluation_id or current.evaluation_id, reason=reason)
        self._atomic(self._state(candidate_id), asdict(updated))
        return updated

    def mark_queued(self, candidate_id: str) -> CandidateState:
        return self._move(candidate_id, {"submitted", "instrument_failure"}, "queued")

    def mark_validating(self, candidate_id: str, evaluation_id: str) -> CandidateState:
        return self._move(candidate_id, {"queued"}, "validating", evaluation_id)

    def record_evaluation(self, candidate_id: str, evaluation_id: str, outcome: str) -> CandidateState:
        if outcome not in {"passed", "failed", "instrument_failure"}:
            raise ValueError(f"invalid evaluation outcome: {outcome}")
        return self._move(candidate_id, {"validating"}, outcome, evaluation_id)

    def reject(self, candidate_id: str, reason: str) -> CandidateState:
        return self._move(candidate_id, {"submitted", "queued", "passed", "failed"}, "rejected", reason=reason)

    def accepted(self) -> AcceptedHead:
        return AcceptedHead(**json.loads(self.accepted_path.read_text(encoding="utf-8")))

    def accept(self, candidate_id: str, expected_head: str) -> AcceptedHead:
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self.accepted()
            state = self.state(candidate_id)
            candidate = self.get(candidate_id)
            if current.revision != expected_head or candidate.base_revision != expected_head:
                raise RuntimeError("accepted head changed")
            if state.status != "passed":
                raise RuntimeError("candidate lacks passed evaluation")
            accepted = AcceptedHead(candidate.revision, candidate_id)
            self._atomic(self.accepted_path, asdict(accepted))
            self._move(candidate_id, {"passed"}, "accepted")
            return accepted
```

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/scientist/test_candidates.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only Candidate files**

```bash
git add scientist/candidates.py tests/scientist/test_candidates.py
git commit -m "feat(scientist): add immutable candidates"
```

---

### Task 5: Serial Authoritative Evaluation

**Files:**
- Create: `scientist/evaluation.py`
- Create: `tests/scientist/test_evaluation.py`

**Interfaces:**
- Consumes: `command: Sequence[str]`、`timeout_seconds: int`、Candidate workspace/revision。
- Produces: `EvaluationQueue.enqueue(candidate_id: str, workspace: Path, revision: str) -> EvaluationRecord`；`tick() -> list[EvaluationRecord]`；`get(evaluation_id: str) -> EvaluationRecord`；`list() -> list[EvaluationRecord]`。
- Protocol: evaluator 在环境变量 `SCIENTIST_EVALUATION_RESULT` 指向的路径写入 `{"outcome":"passed","metrics":{"score":1.0},"evidence":["benchmark:gate"]}` 或把 `outcome` 写为 `failed`，然后以 0 退出；非 0、超时、缺失或非法 JSON 一律为 `instrument_failure`。

- [ ] **Step 1: Write failing serialization and outcome-classification tests**

```python
# tests/scientist/test_evaluation.py
import json
import sys
import time

from scientist.evaluation import EvaluationQueue


def wait_until_finished(queue, evaluation_id):
    for _ in range(200):
        queue.tick()
        record = queue.get(evaluation_id)
        if record.status not in {"queued", "running"}:
            return record
        time.sleep(0.01)
    raise AssertionError("evaluation did not finish")


def test_only_one_authoritative_evaluation_runs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = (
        "import json,os,time; time.sleep(0.1); "
        "json.dump({'outcome':'passed','metrics':{'score':1},'evidence':[]},"
        "open(os.environ['SCIENTIST_EVALUATION_RESULT'],'w'))"
    )
    queue = EvaluationQueue(tmp_path, [sys.executable, "-c", code], timeout_seconds=5)
    first = queue.enqueue("cand-1", workspace, "rev-1")
    second = queue.enqueue("cand-2", workspace, "rev-2")

    queue.tick()
    assert queue.get(first.evaluation_id).status == "running"
    assert queue.get(second.evaluation_id).status == "queued"
    wait_until_finished(queue, first.evaluation_id)
    queue.tick()
    assert queue.get(second.evaluation_id).status == "running"


def test_research_failure_is_distinct_from_instrument_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    failed_code = (
        "import json,os; json.dump({'outcome':'failed','metrics':{},'evidence':['gate']},"
        "open(os.environ['SCIENTIST_EVALUATION_RESULT'],'w'))"
    )
    failed = EvaluationQueue(tmp_path / "a", [sys.executable, "-c", failed_code], 5)
    failed_record = failed.enqueue("cand-1", workspace, "rev-1")
    assert wait_until_finished(failed, failed_record.evaluation_id).status == "failed"

    broken = EvaluationQueue(tmp_path / "b", [sys.executable, "-c", "raise SystemExit(2)"], 5)
    broken_record = broken.enqueue("cand-2", workspace, "rev-2")
    assert wait_until_finished(broken, broken_record.evaluation_id).status == "instrument_failure"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_evaluation.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.evaluation'`.

- [ ] **Step 3: Implement persistent records and a single running process**

```python
# scientist/evaluation.py — data model and core queue methods
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Sequence


EvaluationStatus = Literal["queued", "running", "passed", "failed", "instrument_failure"]


@dataclass(frozen=True)
class EvaluationRecord:
    evaluation_id: str
    candidate_id: str
    workspace: str
    revision: str
    status: EvaluationStatus
    pid: int | None
    started_at: float | None
    finished_at: float | None
    metrics: dict[str, object]
    evidence: tuple[str, ...]
    error: str


class EvaluationQueue:
    def __init__(self, state_dir: Path, command: Sequence[str], timeout_seconds: int):
        if not command or timeout_seconds < 1:
            raise ValueError("evaluation command and positive timeout are required")
        self.root = Path(state_dir) / "evaluations"
        self.root.mkdir(parents=True, exist_ok=True)
        self.command = tuple(command)
        self.timeout_seconds = timeout_seconds
        self._processes: dict[str, subprocess.Popen[str]] = {}

    def _path(self, evaluation_id: str) -> Path:
        return self.root / f"{evaluation_id}.json"

    def _write(self, record: EvaluationRecord) -> None:
        path = self._path(record.evaluation_id)
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(json.dumps(asdict(record), sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def get(self, evaluation_id: str) -> EvaluationRecord:
        row = json.loads(self._path(evaluation_id).read_text(encoding="utf-8"))
        row["evidence"] = tuple(row["evidence"])
        return EvaluationRecord(**row)

    def list(self) -> list[EvaluationRecord]:
        return [self.get(path.stem) for path in sorted(self.root.glob("eval-*.json"))
                if ".result." not in path.name]

    def enqueue(self, candidate_id: str, workspace: Path, revision: str) -> EvaluationRecord:
        number = 1 + max((int(path.stem.split("-")[1]) for path in self.root.glob("eval-*.json")), default=0)
        record = EvaluationRecord(f"eval-{number}", candidate_id, str(Path(workspace).resolve()), revision,
                                  "queued", None, None, None, {}, (), "")
        self._write(record)
        return record

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _finish(self, record: EvaluationRecord, returncode: int | None) -> EvaluationRecord:
        result_path = self.root / f"{record.evaluation_id}.result.json"
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            outcome = payload["outcome"]
            if returncode != 0 or outcome not in {"passed", "failed"}:
                raise ValueError("invalid evaluator result")
            updated = replace(record, status=outcome, pid=None, finished_at=time.time(),
                              metrics=dict(payload.get("metrics", {})),
                              evidence=tuple(str(item) for item in payload.get("evidence", [])))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            updated = replace(record, status="instrument_failure", pid=None, finished_at=time.time(), error=str(exc))
        self._write(updated)
        return updated

    def _start(self, record: EvaluationRecord) -> EvaluationRecord:
        stdout = (self.root / f"{record.evaluation_id}.stdout").open("w", encoding="utf-8")
        stderr = (self.root / f"{record.evaluation_id}.stderr").open("w", encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "SCIENTIST_CANDIDATE_ID": record.candidate_id,
            "SCIENTIST_CANDIDATE_REVISION": record.revision,
            "SCIENTIST_EVALUATION_RESULT": str(self.root / f"{record.evaluation_id}.result.json"),
        })
        process = subprocess.Popen(self.command, cwd=record.workspace, env=env, text=True, stdout=stdout, stderr=stderr)
        self._processes[record.evaluation_id] = process
        updated = replace(record, status="running", pid=process.pid, started_at=time.time())
        self._write(updated)
        return updated

    def tick(self) -> list[EvaluationRecord]:
        changed = []
        running = [row for row in self.list() if row.status == "running"]
        for row in running:
            process = self._processes.get(row.evaluation_id)
            if row.started_at is not None and time.time() - row.started_at > self.timeout_seconds:
                if row.pid is not None and self._pid_alive(row.pid):
                    os.kill(row.pid, signal.SIGTERM)
                changed.append(self._finish(row, None))
            elif process is not None and process.poll() is not None:
                changed.append(self._finish(row, process.returncode))
            elif process is None and (row.pid is None or not self._pid_alive(row.pid)):
                changed.append(self._finish(row, None))
        if not [row for row in self.list() if row.status == "running"]:
            queued = [row for row in self.list() if row.status == "queued"]
            if queued:
                changed.append(self._start(queued[0]))
        return changed
```

- [ ] **Step 4: Add a restart-recovery assertion**

Append this test to `tests/scientist/test_evaluation.py`:

```python
def test_restart_marks_dead_unfinished_process_as_instrument_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    queue = EvaluationQueue(tmp_path, [sys.executable, "-c", "pass"], 5)
    record = queue.enqueue("cand-1", workspace, "rev-1")
    queue.tick()
    process = queue._processes[record.evaluation_id]
    process.wait()

    recovered = EvaluationQueue(tmp_path, [sys.executable, "-c", "pass"], 5)
    recovered.tick()
    assert recovered.get(record.evaluation_id).status == "instrument_failure"
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/scientist/test_evaluation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit only Evaluation files**

```bash
git add scientist/evaluation.py tests/scientist/test_evaluation.py
git commit -m "feat(scientist): serialize authoritative evaluations"
```

---

### Task 6: Lab Runtime Composition and Acceptance Transaction

**Files:**
- Create: `scientist/lab.py`
- Create: `tests/scientist/test_lab.py`

**Interfaces:**
- Consumes: `LabEventStream`、`WorkspaceManager`、`SeatRegistry`、`CandidateStore`、`EvaluationQueue`。
- Produces: `LabRuntime.tick() -> list[LabEvent]`；`board() -> dict[str, object]`；`accept_candidate(candidate_id: str, expected_head: str) -> AcceptedHead`；`reject_candidate(candidate_id: str, reason: str) -> CandidateState`。
- Invariant: live Git HEAD、`CandidateStore.accepted()` 与 `accepted_revision_advanced` event 在恢复后收敛；接纳只允许 fast-forward，不替 Executor 做冲突合并。

- [ ] **Step 1: Write failing evaluation-flow and stale-acceptance tests**

```python
# tests/scientist/test_lab.py
import json
import subprocess
import sys
import time

import pytest

from scientist.lab import LabRuntime


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, text=True, capture_output=True).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "result.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "result.txt")
    git(path, "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD")


def test_candidate_is_evaluated_accepted_and_broadcast(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    evaluator = (
        "import json,os; json.dump({'outcome':'passed','metrics':{'score':2},'evidence':['gate']},"
        "open(os.environ['SCIENTIST_EVALUATION_RESULT'],'w'))"
    )
    lab = LabRuntime(live, tmp_path / "state", tmp_path / "seat-worlds",
                     [sys.executable, "-c", evaluator], 5, 4, 2)
    ref = lab.workspaces.fork("executor-1", base)
    (ref.path / "result.txt").write_text("candidate\n", encoding="utf-8")
    git(ref.path, "add", "result.txt")
    git(ref.path, "commit", "-m", "candidate")
    revision = git(ref.path, "rev-parse", "HEAD")
    candidate = lab.candidates.submit("executor", "executor-1", str(ref.path), base, revision, "score 2", [])

    for _ in range(200):
        lab.tick()
        if lab.candidates.state(candidate.candidate_id).status == "passed":
            break
        time.sleep(0.01)
    accepted = lab.accept_candidate(candidate.candidate_id, base)

    assert accepted.revision == revision
    assert git(live, "rev-parse", "HEAD") == revision
    assert any(event.label == "accepted_revision_advanced" for event in lab.events.read("executor-2"))


def test_stale_candidate_remains_available_but_cannot_replace_head(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    lab = LabRuntime(live, tmp_path / "state", tmp_path / "seat-worlds", [sys.executable, "-c", "pass"], 5, 4, 2)
    first = lab.candidates.submit("executor", "executor-1", "/tmp/w1", base, "rev-1", "first", [])
    second = lab.candidates.submit("executor", "executor-2", "/tmp/w2", base, "rev-2", "second", [])
    lab.candidates.mark_queued(first.candidate_id)
    lab.candidates.mark_validating(first.candidate_id, "eval-1")
    lab.candidates.record_evaluation(first.candidate_id, "eval-1", "passed")
    lab.candidates._atomic(lab.candidates.accepted_path, {"revision": "new-head", "candidate_id": "cand-new"})

    with pytest.raises(RuntimeError, match="accepted head changed"):
        lab.accept_candidate(second.candidate_id, base)
    assert lab.candidates.get(second.candidate_id).claim == "second"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_lab.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.lab'`.

- [ ] **Step 3: Implement the composition root and evaluator state propagation**

```python
# scientist/lab.py
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Sequence

from scientist.candidates import AcceptedHead, CandidateState, CandidateStore
from scientist.evaluation import EvaluationQueue
from scientist.lab_events import LabEvent, LabEventStream
from scientist.seats import SeatRegistry
from scientist.workspaces import WorkspaceManager


class LabRuntime:
    def __init__(self, live_world: Path, state_dir: Path, workspace_root: Path,
                 evaluation_command: Sequence[str], evaluation_timeout: int,
                 executor_slots: int, cognitive_slots: int):
        self.live_world = Path(live_world).resolve()
        initial = subprocess.run(["git", "-C", str(self.live_world), "rev-parse", "HEAD"],
                                 check=True, text=True, capture_output=True).stdout.strip()
        self.events = LabEventStream(state_dir)
        self.workspaces = WorkspaceManager(self.live_world, workspace_root)
        self.seats = SeatRegistry(state_dir, executor_slots, cognitive_slots)
        self.candidates = CandidateStore(state_dir, initial)
        self.evaluations = EvaluationQueue(state_dir, evaluation_command, evaluation_timeout)
        self.acceptance_journal = Path(state_dir) / "acceptance-transaction.json"
        self._recover_acceptance()

    def _write_acceptance_journal(self, candidate_id: str, expected_head: str) -> None:
        temporary = self.acceptance_journal.with_suffix(".tmp")
        temporary.write_text(json.dumps({"candidate_id": candidate_id,
                                         "expected_head": expected_head}), encoding="utf-8")
        temporary.replace(self.acceptance_journal)

    def _recover_acceptance(self) -> None:
        if not self.acceptance_journal.exists():
            return
        row = json.loads(self.acceptance_journal.read_text(encoding="utf-8"))
        candidate = self.candidates.get(row["candidate_id"])
        live_head = subprocess.run(["git", "-C", str(self.live_world), "rev-parse", "HEAD"],
                                   check=True, text=True, capture_output=True).stdout.strip()
        stored_head = self.candidates.accepted().revision
        if live_head == candidate.revision and stored_head == row["expected_head"]:
            self.candidates.accept(candidate.candidate_id, row["expected_head"])
            self.events.append("runtime", "public", "accepted_revision_advanced",
                               {"candidate_id": candidate.candidate_id,
                                "old_revision": row["expected_head"],
                                "new_revision": candidate.revision,
                                "accepted_revision": candidate.revision,
                                "recovered": True},
                               candidate.evidence_refs, workspace=candidate.workspace,
                               revision=candidate.revision)
        elif live_head != row["expected_head"] or stored_head != row["expected_head"]:
            raise RuntimeError("ambiguous acceptance transaction requires operator inspection")
        self.acceptance_journal.unlink()

    def tick(self) -> list[LabEvent]:
        emitted = []
        queued_candidates = [
            self.candidates.get(path.name.removesuffix(".state.json"))
            for path in self.candidates.root.glob("cand-*.state.json")
            if self.candidates.state(path.name.removesuffix(".state.json")).status == "submitted"
        ]
        existing = {row.candidate_id for row in self.evaluations.list()}
        for candidate in queued_candidates:
            if candidate.candidate_id not in existing:
                evaluation = self.evaluations.enqueue(candidate.candidate_id, Path(candidate.workspace), candidate.revision)
                self.candidates.mark_queued(candidate.candidate_id)
                emitted.append(self.events.append("evaluator", "public", "evaluation_queued",
                                                  {"candidate_id": candidate.candidate_id,
                                                   "evaluation_id": evaluation.evaluation_id}))
        for evaluation in self.evaluations.tick():
            if evaluation.status == "running" and self.candidates.state(evaluation.candidate_id).status == "queued":
                self.candidates.mark_validating(evaluation.candidate_id, evaluation.evaluation_id)
                emitted.append(self.events.append("evaluator", "public", "evaluation_started",
                                                  {"candidate_id": evaluation.candidate_id,
                                                   "evaluation_id": evaluation.evaluation_id},
                                                  workspace=evaluation.workspace,
                                                  revision=evaluation.revision))
            elif evaluation.status in {"passed", "failed", "instrument_failure"}:
                self.candidates.record_evaluation(evaluation.candidate_id, evaluation.evaluation_id, evaluation.status)
                emitted.append(self.events.append("evaluator", "public", "evaluation_finished",
                                                  {"candidate_id": evaluation.candidate_id,
                                                   "evaluation_id": evaluation.evaluation_id,
                                                   "outcome": evaluation.status,
                                                   "metrics": evaluation.metrics,
                                                   "error": evaluation.error},
                                                  evaluation.evidence,
                                                  workspace=evaluation.workspace,
                                                  revision=evaluation.revision))
        return emitted

    def accept_candidate(self, candidate_id: str, expected_head: str) -> AcceptedHead:
        candidate = self.candidates.get(candidate_id)
        if self.candidates.accepted().revision != expected_head or candidate.base_revision != expected_head:
            raise RuntimeError("accepted head changed")
        if self.candidates.state(candidate_id).status != "passed":
            raise RuntimeError("candidate lacks passed evaluation")
        live_dirty = subprocess.run(
            ["git", "-C", str(self.live_world), "status", "--porcelain", "--untracked-files=no"],
            check=True, text=True, capture_output=True,
        ).stdout.strip()
        if live_dirty:
            raise RuntimeError("accepted world has uncommitted tracked changes")
        self._write_acceptance_journal(candidate_id, expected_head)
        subprocess.run(["git", "-C", str(self.live_world), "fetch", candidate.workspace, candidate.revision],
                       check=True, text=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.live_world), "merge", "--ff-only", candidate.revision],
                       check=True, text=True, capture_output=True)
        accepted = self.candidates.accept(candidate_id, expected_head)
        self.acceptance_journal.unlink()
        evaluation = self.evaluations.get(self.candidates.state(candidate_id).evaluation_id)
        changed_paths = subprocess.run(
            ["git", "-C", str(self.live_world), "diff", "--name-only", expected_head, accepted.revision],
            check=True, text=True, capture_output=True,
        ).stdout.splitlines()
        self.events.append(
            "sci", "public", "accepted_revision_advanced",
            {"candidate_id": candidate_id, "old_revision": expected_head,
             "new_revision": accepted.revision, "accepted_revision": accepted.revision,
             "originating_executor": candidate.owner_id, "changed_paths": changed_paths,
             "claim": candidate.claim, "gates": list(evaluation.evidence),
             "metrics": evaluation.metrics, "migration_notes": list(candidate.open_questions)},
            candidate.evidence_refs, workspace=candidate.workspace, revision=accepted.revision,
        )
        return accepted

    def reject_candidate(self, candidate_id: str, reason: str) -> CandidateState:
        state = self.candidates.reject(candidate_id, reason)
        self.events.append("sci", "public", "candidate_rejected", {"candidate_id": candidate_id, "reason": reason})
        return state

    def board(self) -> dict[str, object]:
        return {
            "accepted": self.candidates.accepted(),
            "seats": self.seats.list(),
            "candidates": [{"candidate": self.candidates.get(path.name.removesuffix(".core.json")),
                            "state": self.candidates.state(path.name.removesuffix(".core.json"))}
                           for path in sorted(self.candidates.root.glob("cand-*.core.json"))],
            "evaluations": self.evaluations.list(),
        }
```

- [ ] **Step 4: Add an acceptance crash-recovery test**

Append this test after the first Lab acceptance test:

```python
def test_restart_recovers_git_advance_recorded_in_acceptance_journal(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    command = [sys.executable, "-c", "pass"]
    lab = LabRuntime(live, tmp_path / "state", tmp_path / "seat-worlds", command, 5, 4, 2)
    ref = lab.workspaces.fork("executor-1", base)
    (ref.path / "result.txt").write_text("candidate\n", encoding="utf-8")
    git(ref.path, "add", "result.txt")
    git(ref.path, "commit", "-m", "candidate")
    revision = git(ref.path, "rev-parse", "HEAD")
    candidate = lab.candidates.submit("executor", "executor-1", str(ref.path), base, revision, "claim", [])
    lab.candidates.mark_queued(candidate.candidate_id)
    lab.candidates.mark_validating(candidate.candidate_id, "eval-1")
    lab.candidates.record_evaluation(candidate.candidate_id, "eval-1", "passed")
    lab._write_acceptance_journal(candidate.candidate_id, base)
    git(live, "fetch", str(ref.path), revision)
    git(live, "merge", "--ff-only", revision)

    recovered = LabRuntime(live, tmp_path / "state", tmp_path / "seat-worlds", command, 5, 4, 2)

    assert recovered.candidates.accepted().revision == revision
    assert not recovered.acceptance_journal.exists()
```

- [ ] **Step 5: Correct the stale test to exercise the public transition contract**

Before the rejection assertion, mark `second` evaluated so the only rejection reason is staleness:

```python
    lab.candidates.mark_queued(second.candidate_id)
    lab.candidates.mark_validating(second.candidate_id, "eval-2")
    lab.candidates.record_evaluation(second.candidate_id, "eval-2", "passed")
```

- [ ] **Step 6: Run primitive and Lab tests together**

Run: `python -m pytest tests/scientist/test_lab_events.py tests/scientist/test_workspaces.py tests/scientist/test_seats.py tests/scientist/test_candidates.py tests/scientist/test_evaluation.py tests/scientist/test_lab.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the composition root**

```bash
git add scientist/lab.py tests/scientist/test_lab.py
git commit -m "feat(scientist): compose portfolio lab runtime"
```

---

### Task 7: Seat-Side CLI and Role Permissions

**Files:**
- Create: `scientist/lab_cli.py`
- Create: `tests/scientist/test_lab_cli.py`

**Interfaces:**
- Consumes environment: `SCIENTIST_STATE_DIR`、`SCIENTIST_LIVE_WORLD`、`SCIENTIST_WORKSPACE_ROOT`、`SCIENTIST_SEAT_ID`、`SCIENTIST_SEAT_ROLE`。
- Produces commands: `status`、`events read`、`events publish`、`checkpoint`、`candidate submit`、`sync status`、`sync accepted`。`status` 只投影 accepted head、活动路线摘要、Candidate 摘要和调用席位同步状态，不创建第六种状态。
- Output: every successful command writes exactly one JSON object to stdout; errors write one line to stderr and exit 2.

- [ ] **Step 1: Write failing CLI permission and event tests**

```python
# tests/scientist/test_lab_cli.py
import json

from scientist.lab_cli import main


def configure(monkeypatch, tmp_path, role="executor"):
    monkeypatch.setenv("SCIENTIST_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SCIENTIST_LIVE_WORLD", str(tmp_path / "live"))
    monkeypatch.setenv("SCIENTIST_WORKSPACE_ROOT", str(tmp_path / "worlds"))
    monkeypatch.setenv("SCIENTIST_SEAT_ID", f"{role}-1")
    monkeypatch.setenv("SCIENTIST_SEAT_ROLE", role)


def test_any_seat_can_publish_and_read_same_event_stream(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path, "reviewer")
    assert main(["events", "publish", "--audience", "executor-1", "--label", "dissent",
                 "--payload", '{"claim":"unsafe"}']) == 0
    configure(monkeypatch, tmp_path, "executor")
    assert main(["events", "read"]) == 0
    row = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert row["events"][0]["label"] == "dissent"


def test_cognitive_seat_cannot_submit_production_candidate(monkeypatch, tmp_path, capsys):
    configure(monkeypatch, tmp_path, "challenger")
    assert main(["candidate", "submit", "--base", "base", "--revision", "rev",
                 "--claim", "claim"]) == 2
    assert "only executor" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/scientist/test_lab_cli.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'scientist.lab_cli'`.

- [ ] **Step 3: Implement a thin argparse adapter over the primitives**

```python
# scientist/lab_cli.py — parser-independent dispatch core
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from scientist.candidates import CandidateStore
from scientist.lab_events import LabEventStream
from scientist.workspaces import WorkspaceManager


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing {name}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scientist.lab_cli")
    groups = parser.add_subparsers(dest="group", required=True)
    groups.add_parser("status")
    events = groups.add_parser("events").add_subparsers(dest="action", required=True)
    events.add_parser("read").add_argument("--limit", type=int, default=100)
    publish = events.add_parser("publish")
    publish.add_argument("--audience", action="append", required=True)
    publish.add_argument("--label", required=True)
    publish.add_argument("--payload", required=True)
    publish.add_argument("--revision")
    groups.add_parser("checkpoint")
    candidate = groups.add_parser("candidate").add_subparsers(dest="action", required=True)
    submit = candidate.add_parser("submit")
    submit.add_argument("--base", required=True)
    submit.add_argument("--revision", required=True)
    submit.add_argument("--claim", required=True)
    submit.add_argument("--evidence", action="append", default=[])
    submit.add_argument("--change-summary", default="")
    submit.add_argument("--quick-check", action="append", default=[])
    submit.add_argument("--risk", action="append", default=[])
    submit.add_argument("--open-question", action="append", default=[])
    submit.add_argument("--verify-with", action="append", default=[])
    sync = groups.add_parser("sync").add_subparsers(dest="action", required=True)
    sync.add_parser("status")
    sync.add_parser("accepted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        state = Path(_env("SCIENTIST_STATE_DIR"))
        live = Path(_env("SCIENTIST_LIVE_WORLD"))
        worlds = Path(_env("SCIENTIST_WORKSPACE_ROOT"))
        seat_id = _env("SCIENTIST_SEAT_ID")
        role = _env("SCIENTIST_SEAT_ROLE")
        if args.group == "candidate" and role != "executor":
            raise PermissionError("only executor may submit a production candidate")
        events = LabEventStream(state)
        if args.group == "events" and args.action == "publish":
            audience = "public" if args.audience == ["public"] else args.audience
            result = asdict(events.append(seat_id, audience, args.label, json.loads(args.payload), revision=args.revision))
        elif args.group == "events":
            rows = events.read(seat_id, limit=args.limit)
            if rows:
                events.acknowledge(seat_id, rows[-1].event_id)
            result = {"events": [asdict(row) for row in rows]}
        else:
            workspaces = WorkspaceManager(live, worlds)
            accepted_path = state / "candidates" / "accepted.json"
            initial = json.loads(accepted_path.read_text(encoding="utf-8"))["revision"]
            candidates = CandidateStore(state, initial)
        if args.group == "checkpoint":
            result = {"revision": workspaces.checkpoint(seat_id)}
        elif args.group == "candidate":
            workspace = str(workspaces._load(seat_id).path)
            head = subprocess.run(["git", "-C", workspace, "rev-parse", "HEAD"], check=True,
                                  text=True, capture_output=True).stdout.strip()
            dirty = subprocess.run(["git", "-C", workspace, "status", "--porcelain", "--untracked-files=no"],
                                   check=True, text=True, capture_output=True).stdout.strip()
            if head != args.revision or dirty:
                raise ValueError("candidate must name the clean owned workspace HEAD")
            if subprocess.run(["git", "-C", workspace, "cat-file", "-e", f"{args.revision}^{{commit}}"],
                              text=True, capture_output=True).returncode != 0:
                raise ValueError("candidate revision does not exist in owned workspace")
            if subprocess.run(["git", "-C", workspace, "merge-base", "--is-ancestor", args.base, args.revision],
                              text=True, capture_output=True).returncode != 0:
                raise ValueError("candidate revision does not descend from base revision")
            submitted = candidates.submit(
                role, seat_id, workspace, args.base, args.revision, args.claim, args.evidence,
                args.change_summary, args.quick_check, args.risk, args.open_question, args.verify_with,
            )
            events.append(seat_id, "public", "candidate_submitted",
                          {"candidate_id": submitted.candidate_id, "claim": submitted.claim},
                          submitted.evidence_refs, workspace=workspace, revision=args.revision)
            result = asdict(submitted)
        elif args.group == "sync":
            accepted = candidates.accepted().revision
            revision = (workspaces.sync_status(seat_id, accepted) if args.action == "status"
                        else workspaces.sync_from_accepted(seat_id, accepted))
            result = {"accepted_revision": accepted, "result": revision}
            if args.action == "accepted":
                events.append(seat_id, "public", "incorporates",
                              {"accepted_revision": accepted, "new_head": revision},
                              workspace=str(workspaces._load(seat_id).path), revision=revision)
        elif args.group == "status":
            seat_rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((state / "seats").glob("*.json"))]
            candidate_rows = [json.loads(path.read_text(encoding="utf-8"))
                              for path in sorted((state / "candidates").glob("*.core.json"))]
            accepted = candidates.accepted().revision
            result = {"accepted_head": accepted,
                      "active_lines": [{"seat_id": row["seat_id"], "charter": row["charter"]}
                                       for row in seat_rows if row["role"] == "executor" and row["status"] == "active"],
                      "candidates": [{"candidate_id": row["candidate_id"], "owner_id": row["owner_id"],
                                      "claim": row["claim"], "base_revision": row["base_revision"],
                                      "stale_base": row["base_revision"] != accepted}
                                     for row in candidate_rows],
                      "sync_facts": workspaces.sync_facts(seat_id, accepted) if role == "executor" else None,
                      "unread_events": events.unread_count(seat_id)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI and primitive tests**

Run: `python -m pytest tests/scientist/test_lab_cli.py tests/scientist/test_lab_events.py tests/scientist/test_workspaces.py tests/scientist/test_candidates.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only CLI files**

```bash
git add scientist/lab_cli.py tests/scientist/test_lab_cli.py
git commit -m "feat(scientist): expose seat-side lab primitives"
```

---

### Task 8: Integrate Long-Lived Seats into `InWorldAssistant`

**Files:**
- Modify: `scientist/assistant_tools.py`
- Modify: `scientist/collaboration.py`
- Modify: `tests/scientist/test_oneworld.py`
- Modify: `tests/scientist/test_collaboration.py`

**Interfaces:**
- Consumes: `LabRuntime` from Task 6; `SeatRegistry` and `WorkspaceManager` records; `LabEventStream.read/acknowledge` from Task 1。
- Produces: `InWorldAssistant.launch_async(role: str, brief: str, audience: str = "sci", source_revision: str | None = None) -> str` returns engagement id；`resume_seat(seat_id: str, brief: str) -> str`（允许 Executor 与需持续事实调查的 Searcher）；`park_executor(seat_id: str, reason: str) -> dict[str, object]`；`retire_executor(seat_id: str, reason: str) -> dict[str, object]`。
- Manifest contract: every engagement records `seat_id`, `engagement_id`, `role`, `workspace`, `base_revision`, `session_id`, `pid`, `status`; Executor resumes preserve `seat_id` and workspace but receive a new `engagement_id`.

- [ ] **Step 1: Write failing default-isolation, stable-resume and restart tests**

Add this fixture and these tests beside the existing `_fake_claude` helper in `tests/scientist/test_oneworld.py`:

```python
@pytest.fixture()
def lab_assistant(tmp_path):
    import subprocess
    import sys

    from scientist.assistant_tools import AssistantConfig, InWorldAssistant
    from scientist.lab import LabRuntime

    world = _world(tmp_path)
    subprocess.run(["git", "-C", str(world.work), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(world.work), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(world.work), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(world.work), "add", "note.txt"], check=True)
    subprocess.run(["git", "-C", str(world.work), "commit", "-m", "base"], check=True, capture_output=True)
    evaluator = (
        "import json,os; json.dump({'outcome':'passed','metrics':{},'evidence':[]},"
        "open(os.environ['SCIENTIST_EVALUATION_RESULT'],'w'))"
    )
    lab = LabRuntime(world.work, world.state_dir, world.scratch / "seat-worlds",
                     [sys.executable, "-c", evaluator], 5, 4, 2)
    return InWorldAssistant(
        world=world,
        config=AssistantConfig(model="deepseek-v4-flash", effort="medium",
                               command=str(_fake_claude(tmp_path)), work_default_minutes=1),
        ledger=LocalLedger(world.state_dir), lab=lab, episode_id="t",
    )


def test_executor_launch_owns_an_isolated_workspace(lab_assistant):
    engagement_id = lab_assistant.launch("executor", "own route A")
    manifest = lab_assistant.manifest(engagement_id)
    seat = lab_assistant.lab.seats.get(manifest["seat_id"])

    assert manifest["workspace"] != str(lab_assistant.world.work)
    assert seat.role == "executor"
    assert seat.charter == "own route A"
    assert seat.workspace == manifest["workspace"]


def test_resume_preserves_executor_identity_and_workspace(lab_assistant):
    first_engagement = lab_assistant.launch("executor", "own route A")
    first = lab_assistant.manifest(first_engagement)
    lab_assistant.collect(first_engagement)
    lab_assistant.park_executor(first["seat_id"], "waiting")

    second_engagement = lab_assistant.resume_seat(first["seat_id"], "new evidence arrived")
    second = lab_assistant.manifest(second_engagement)

    assert second["seat_id"] == first["seat_id"]
    assert second["workspace"] == first["workspace"]
    assert second["engagement_id"] != first["engagement_id"]
    assert second["continued_from"] == first["session_id"]


def test_reconcile_adopts_live_process_instead_of_killing_it(lab_assistant, monkeypatch):
    engagement_id = lab_assistant.launch_async("executor", "own route A")
    manifest = lab_assistant.manifest(engagement_id)
    monkeypatch.setattr(lab_assistant, "_pid_alive", lambda pid: pid == manifest["pid"])
    monkeypatch.setattr(lab_assistant, "_terminate_pid", lambda pid: (_ for _ in ()).throw(AssertionError("must not kill")))

    lab_assistant._reconcile()

    assert lab_assistant.manifest(engagement_id)["status"] == "running"
```

Add this focused prompt assertion to `tests/scientist/test_collaboration.py`:

```python
def test_executor_prompt_teaches_natural_event_checkpoints_and_ownership():
    prompt = station_prompt("executor", seat_id="executor-1", charter="own route A",
                            workspace="/tmp/e1", base_revision="abc", unread_events=[],
                            objective="optimize", hard_constraints="gate must pass")
    assert "python -m scientist.lab_cli events read" in prompt
    assert "Do not wait for SCI approval inside your research line" in prompt
    assert "Only you may write this workspace" in prompt
```

- [ ] **Step 2: Run tests to verify the new contract fails**

Run: `python -m pytest tests/scientist/test_oneworld.py -k 'isolated_workspace or stable_resume or adopts_live_process' -q && python -m pytest tests/scientist/test_collaboration.py -k natural_event_checkpoints -q`

Expected: FAIL because `lab`, `manifest`, `resume_seat` and the expanded `station_prompt` contract do not exist.

- [ ] **Step 3: Extend configuration and constructor injection**

Add these exact fields to the existing `AssistantConfig` without removing its current model, timeout, budget, goal or gate fields; add the constructor assignment after the existing ledger assignment:

```python
executor_slots: int = 4
cognitive_slots: int = 2

# Add `lab: LabRuntime` as a keyword-only constructor argument after `ledger`.
self.lab = lab
```

In `AssistantConfig.from_spec`, read only the two bounded integers below; reject negative cognitive capacity and executor capacity below one:

```python
executor_slots = int(assistant_spec.get("executor_slots", 4))
cognitive_slots = int(assistant_spec.get("cognitive_slots", 2))
if executor_slots < 1 or cognitive_slots < 0:
    raise ValueError("assistant seat capacities must be executor>=1 and cognitive>=0")
```

Insert these two keyword arguments immediately before the closing parenthesis of the existing `return cls(` call:

```python
executor_slots=executor_slots,
cognitive_slots=cognitive_slots,
```

- [ ] **Step 4: Replace per-call workspace selection with stable seat allocation**

In `launch`, allocate identity before rendering the prompt and use the accepted revision as the default source:

```python
accepted_revision = source_revision or self.lab.candidates.accepted().revision
if role == "executor":
    seat = self.lab.seats.create("executor", None, brief)
    workspace_ref = self.lab.workspaces.fork(seat.seat_id, accepted_revision)
    workspace = workspace_ref.path
    seat = self.lab.seats.set_workspace(seat.seat_id, str(workspace))
else:
    seat = self.lab.seats.create(role, None, brief)
    workspace = self._seat_roots()[0] / seat.seat_id
    workspace.mkdir(parents=True, exist_ok=True)
engagement_id = self._next_call_id(role)
unread = self.lab.events.read(seat.seat_id)
prompt = station_prompt(role, seat_id=seat.seat_id, charter=brief, workspace=str(workspace),
                        base_revision=accepted_revision, unread_events=unread,
                        objective=self.config.goal, hard_constraints=self.config.gate_block)
```

Add this method to `SeatRegistry` in `scientist/seats.py` and its assertion to `tests/scientist/test_seats.py`:

```python
def set_workspace(self, seat_id: str, workspace: str) -> SeatRecord:
    current = self.get(seat_id)
    if current.workspace is not None and current.workspace != workspace:
        raise ValueError(f"workspace ownership is immutable: {seat_id}")
    updated = replace(current, workspace=workspace)
    self._write(updated)
    return updated
```

Populate the child environment without adding another transport:

```python
child_env.update({
    "SCIENTIST_STATE_DIR": str(self.lab.events.root.parent),
    "SCIENTIST_LIVE_WORLD": str(self.world.work),
    "SCIENTIST_WORKSPACE_ROOT": str(self.lab.workspaces.root),
    "SCIENTIST_SEAT_ID": seat.seat_id,
    "SCIENTIST_SEAT_ROLE": role,
})
```

After `Popen`, persist the manifest and attach the engagement atomically under `_engagement_lock`. A successful prompt launch acknowledges only the events embedded in that prompt:

```python
manifest["audience"] = [audience] if isinstance(audience, str) else list(audience)
manifest["base_revision"] = accepted_revision
self.lab.seats.attach_engagement(seat.seat_id, engagement_id, process.pid)
self.lab.events.append(
    "runtime", "public", "seat_started",
    {"seat_id": seat.seat_id, "engagement_id": engagement_id,
     "role": role, "charter": brief, "base_revision": accepted_revision},
    workspace=str(workspace), revision=accepted_revision,
)
if unread:
    self.lab.events.acknowledge(seat.seat_id, unread[-1].event_id)
```

- [ ] **Step 5: Make cognitive experiments revision-pinned**

Replace `_write_experiment_kit` command construction with:

```python
source_world = self.lab.workspaces.source_for_revision(base_revision)
command = [
    sys.executable, "-m", "scientist.mkexp",
    "--source", str(source_world),
    "--dest", str(experiment_dir),
    "--revision", base_revision,
]
```

The generated instruction must state that the cognitive seat may experiment only inside that disposable directory and must publish evidence for an Executor to absorb; it must not call `candidate submit`.

- [ ] **Step 6: Implement stable resume, park and retire without a second session system**

Reuse the existing Claude session id and existing `continue_engagement` process-launch body in this replacement API:

```python
def resume_seat(self, seat_id: str, brief: str) -> str:
    seat = self.lab.seats.get(seat_id)
    if seat.role not in {"executor", "searcher"} or seat.status == "retired":
        raise ValueError(f"seat cannot resume: {seat_id}")
    previous = self._latest_manifest_for_seat(seat_id)
    return self._launch_existing_seat(seat, brief, continued_from=previous["session_id"])

def park_executor(self, seat_id: str, reason: str) -> dict[str, object]:
    seat = self.lab.seats.get(seat_id)
    if seat.engagement_id and self.manifest(seat.engagement_id)["status"] == "running":
        self.cancel(seat.engagement_id)
    updated = self.lab.seats.transition(seat_id, "parked", reason)
    self.lab.events.append("sci", "public", "seat_parked", {"seat_id": seat_id, "reason": reason})
    return asdict(updated)

def retire_executor(self, seat_id: str, reason: str) -> dict[str, object]:
    self.park_executor(seat_id, reason)
    updated = self.lab.seats.transition(seat_id, "retired", reason)
    self.lab.events.append("sci", "public", "seat_retired", {"seat_id": seat_id, "reason": reason})
    return asdict(updated)
```

`_launch_existing_seat` must call the same internal process launcher as `launch`; it must not fork a new workspace or create a second Seat. `_latest_manifest_for_seat` returns the manifest with the greatest `started_at` whose `seat_id` matches, and raises `KeyError` if none exists.

- [ ] **Step 7: Preserve live processes across SCI restart**

Change `_reconcile` so a manifest with `status == "running"` and a live pid remains running even when no local `Popen` object exists. When the pid is dead, reuse the current salvage/collect path. Remove the branch that kills an unowned but live pid.

```python
if manifest["status"] == "running" and self._pid_alive(int(manifest["pid"])):
    continue
if manifest["status"] == "running":
    self._collect_or_salvage(manifest["engagement_id"])
```

- [ ] **Step 8: Replace station prompts with the common lab constitution**

Use one common block for every role in `scientist/collaboration.py`:

```python
LAB_CONSTITUTION = """You are seat {seat_id} in a Scientist-managed research lab.
Objective: {objective}
Hard constraints and authoritative gates: {hard_constraints}
Your source revision is {base_revision}.
SCI owns portfolio direction, resource allocation, and the accepted head.
Each workspace has exactly one owner. Never edit another seat's workspace.
All inter-seat information uses the Lab Event Stream. Event labels are descriptive data, not approval stages.
Read new events at natural checkpoints with: python -m scientist.lab_cli events read
Inspect the accepted head, active research-line summaries, Candidates, and your sync relation with: python -m scientist.lab_cli status
Publish useful evidence immediately with: python -m scientist.lab_cli events publish
"""

EXECUTOR_CONTRACT = """You own this research line: {charter}
Do not wait for SCI approval inside your research line. Choose experiments and implementation autonomously.
Only you may write this workspace: {workspace}
Before a production Candidate, create a clean checkpoint and submit it through scientist.lab_cli.
An accepted-head event is information, not an automatic rebase. Continue, sync, or request a restart based on evidence.
"""
```

Searcher, Proposer, Challenger and Reviewer append role-specific advice to `LAB_CONSTITUTION`; all four must state that they are temporary cognitive seats, may publish directly to the relevant Executor, and cannot submit production Candidates.

- [ ] **Step 9: Publish completed reports through Event and release capacity**

After the existing report parser has persisted its raw artifact, make `collect` publish the bounded digest to the manifest audience. Executor completion becomes `parked`; a completed Searcher also becomes `parked` so SCI may resume the same factual inquiry; Proposer, Challenger and Reviewer become `retired`.

```python
audience = manifest.get("audience") or ["sci"]
self.lab.events.append(
    seat.seat_id, audience, "seat_report",
    {"role": seat.role, "status": report["status"],
     "summary": report.get("self_report_digest", ""),
     "diff_summary": report.get("diff_summary", ""),
     "metrics": report.get("metrics", {})},
    workspace=seat.workspace,
    revision=report.get("revision"),
)
target = "parked" if seat.role in {"executor", "searcher"} else "retired"
self.lab.seats.transition(seat.seat_id, target, "engagement completed")
```

`sweep()` returns engagement lifecycle receipts to its caller for bookkeeping, but the SCI model and other seats receive report content only by reading the Event stream.

- [ ] **Step 10: Run assistant and collaboration regressions**

Run: `python -m pytest tests/scientist/test_seats.py tests/scientist/test_oneworld.py tests/scientist/test_async_runtime.py tests/scientist/test_collaboration.py -q`

Expected: PASS. Delete obsolete assertions that Executor defaults to `workspace="current"`; do not preserve that unsafe mode behind a compatibility flag.

- [ ] **Step 11: Commit the assistant integration**

```bash
git add scientist/seats.py scientist/assistant_tools.py scientist/collaboration.py tests/scientist/test_seats.py tests/scientist/test_oneworld.py tests/scientist/test_async_runtime.py tests/scientist/test_collaboration.py
git commit -m "feat(scientist): run stable isolated research seats"
```

---

### Task 9: SCI Portfolio Control Loop and Minimal Native Tools

**Files:**
- Modify: `scientist/native_tools.py`
- Modify: `scientist/agent.py`
- Modify: `scientist/cli.py`
- Modify: `scientist/prompts/scientist.md`
- Modify: `scientist/prompts/scientist_code.md`
- Modify: `scientist/prompts/research_team.md`
- Modify: `tests/scientist/test_model_native_tools.py`
- Modify: `tests/scientist/test_async_runtime.py`
- Modify: `tests/scientist/test_context_contract.py`

**Interfaces:**
- Consumes: `LabRuntime.tick/board/accept_candidate/reject_candidate` and Task 8 assistant lifecycle methods.
- Produces SCI tools: existing role launch tools plus `resume_seat`、`park_executor`、`retire_executor`、`accept_candidate`、`reject_candidate`、`publish_lab_event`、existing `wait_engagement`、`monitor_engagement`、`cancel_engagement`。
- Removes: synchronous Reviewer dispatch, `reviewer_heard_after`, Reviewer listen refusal counter/gate, Executor `workspace=current|isolated` argument, old `continue_engagement` public tool.

- [ ] **Step 1: Write failing tool-surface and async-Reviewer tests**

```python
# tests/scientist/test_model_native_tools.py
def test_portfolio_tools_are_small_and_role_neutral():
    names = {tool["name"] for tool in NATIVE_TOOLS}
    assert {"resume_seat", "park_executor", "retire_executor",
            "accept_candidate", "reject_candidate", "publish_lab_event"} <= names
    assert "continue_engagement" not in names
    assert "approve_review" not in names
    assert "submit_finding" not in names
    executor = next(tool for tool in NATIVE_TOOLS if tool["name"] == "executor")
    assert "workspace" not in executor["input_schema"]["properties"]


# tests/scientist/test_async_runtime.py
def test_reviewer_launch_is_async_like_every_other_cognitive_seat():
    from scientist.agent import dispatch_action

    class Assistant:
        def __init__(self):
            self.calls = []

        def launch_async(self, role, brief, audience, source_revision):
            self.calls.append((role, brief, audience, source_revision))
            return "reviewer-001"

        def pending(self, role=None):
            return [{"role": "reviewer"}] if role == "reviewer" else []

    assistant = Assistant()
    engagement_id = dispatch_action(
        {"action": "reviewer", "brief": "audit wall"},
        world=None, assistant=assistant, ledger=None, lab=None,
    )
    assert engagement_id.startswith("reviewer-")
    assert assistant.pending(role="reviewer")
```

- [ ] **Step 2: Run tests to verify the old control surface fails**

Run: `python -m pytest tests/scientist/test_model_native_tools.py -k portfolio_tools -q && python -m pytest tests/scientist/test_async_runtime.py -k reviewer_launch_is_async -q`

Expected: FAIL because the old tool surface and synchronous Reviewer branch remain.

- [ ] **Step 3: Define the minimal SCI schemas**

Add only these control schemas to `scientist/native_tools.py`; use the same `seat_id`, `candidate_id`, `expected_head`, `reason`, `audience`, `label`, `payload` names used by Tasks 1–8:

```python
PORTFOLIO_TOOLS = (
    native_tool("resume_seat", "Resume a parked Executor or a Searcher continuing the same factual inquiry.",
                {"seat_id": string_field(), "brief": string_field()}, ["seat_id", "brief"]),
    native_tool("park_executor", "Stop spending compute while preserving the Executor's line and workspace.",
                {"seat_id": string_field(), "reason": string_field()}, ["seat_id", "reason"]),
    native_tool("retire_executor", "End a research line while preserving its evidence and workspace.",
                {"seat_id": string_field(), "reason": string_field()}, ["seat_id", "reason"]),
    native_tool("accept_candidate", "Fast-forward the accepted head to a passed, non-stale Candidate.",
                {"candidate_id": string_field(), "expected_head": string_field()}, ["candidate_id", "expected_head"]),
    native_tool("reject_candidate", "Reject a Candidate without deleting it.",
                {"candidate_id": string_field(), "reason": string_field()}, ["candidate_id", "reason"]),
    native_tool("publish_lab_event", "Publish one public or targeted fact on the unified Lab Event Stream.",
                {"audience": audience_field(), "label": string_field(), "payload": object_field()},
                ["audience", "label", "payload"]),
)
```

Remove `workspace` from the Executor schema. Keep role launch, wait, monitor and cancel tools because they are orthogonal lifecycle operations, not new collaboration wiring.

Add optional `source_revision` and `target_seat_id` string properties to Searcher, Proposer, Challenger and Reviewer. `source_revision` must be an explicit accepted revision, Candidate revision or clean Executor checkpoint; `target_seat_id` makes the completed report audience `[target_seat_id, "sci"]`. These are common Event/Workspace parameters, not role-specific channels.

- [ ] **Step 4: Route every role asynchronously and map control tools directly**

Replace the Reviewer special case in `dispatch_action` with the same async path as other role launches:

```python
if name in {"searcher", "proposer", "executor", "challenger", "reviewer"}:
    target = arguments.get("target_seat_id")
    audience = [target, "sci"] if target else ["sci"]
    return self.assistant.launch_async(
        name, arguments["brief"], audience=audience,
        source_revision=arguments.get("source_revision"),
    )
if name == "resume_seat":
    return self.assistant.resume_seat(arguments["seat_id"], arguments["brief"])
if name == "park_executor":
    return self.assistant.park_executor(arguments["seat_id"], arguments["reason"])
if name == "retire_executor":
    return self.assistant.retire_executor(arguments["seat_id"], arguments["reason"])
if name == "accept_candidate":
    return asdict(self.lab.accept_candidate(arguments["candidate_id"], arguments["expected_head"]))
if name == "reject_candidate":
    return asdict(self.lab.reject_candidate(arguments["candidate_id"], arguments["reason"]))
if name == "publish_lab_event":
    return asdict(self.lab.events.append("sci", arguments["audience"], arguments["label"], arguments["payload"]))
```

Delete `_LISTEN_REFUSAL_MAX`, Reviewer listen state, `reviewer_heard_after` validation and the branch that calls `assistant.engage` with the literal role `"reviewer"`. `validate_conclusion` may still enforce existing evidence/research-state requirements, but Reviewer participation is never mandatory.

- [ ] **Step 5: Tick the Lab and derive one portfolio board each SCI turn**

At the top of every `run_episode` turn, before model input construction:

```python
lab_events = self.lab.tick()
self.assistant.sweep()
inbox = self.lab.events.read("sci")
if inbox:
    self.lab.events.acknowledge("sci", inbox[-1].event_id)
portfolio = self.lab.board()
turn_context = {
    "portfolio": serialize_board(portfolio),
    "new_lab_events": [asdict(event) for event in inbox],
}
```

`lab_events` is retained only for runtime logging; every cross-seat report entering model context comes from `inbox`, proving that the Event stream is the single information path.

When `_run_actions` receives one model batch, emit one mechanical event before starting its role calls. Maintain `local_tool_calls_before_first_dispatch` as an in-memory/session counter incremented only for SCI `bash`、read、search and write tools; it is telemetry and never blocks dispatch:

```python
role_names = {"searcher", "proposer", "executor", "challenger", "reviewer"}
role_actions = [action for action in actions if action.get("action") in role_names]
if role_actions:
    lab.events.append(
        "runtime", "public", "dispatch_batch",
        {"count": len(role_actions),
         "roles": [action["action"] for action in role_actions],
         "local_tool_calls_before_first_dispatch": local_tool_calls_before_first_dispatch},
    )
```

The first `dispatch_batch.created_at` is `first_dispatch_at`; its `count` is the first batch size. Seat manifests, Event timestamps/cursors, Candidate events and Evaluation timestamps provide the remaining efficiency readouts without a scheduling database.

`serialize_board` must expose only derived facts:

```python
def serialize_board(board: dict[str, object]) -> dict[str, object]:
    accepted = board["accepted"]
    return {
        "accepted_head": asdict(accepted),
        "seats": [asdict(seat) for seat in board["seats"]],
        "candidates": [{"candidate": asdict(row["candidate"]), "state": asdict(row["state"])}
                       for row in board["candidates"]],
        "evaluations": [asdict(row) for row in board["evaluations"]],
    }
```

Do not persist `priority`, `dependency_graph`, `line_score` or another authoritative portfolio file. SCI infers investment decisions from this board plus events and research memory.

- [ ] **Step 6: Encode the Research Portfolio operating philosophy in prompts**

Add this exact policy block to both Scientist prompts and summarize it in `research_team.md`:

```text
Manage a research portfolio, not a serial task queue.
Ask what valuable work should already be happening now.
Parallelize independent learning opportunities; serialize only true dependencies.
An Executor owns a durable research line, not a small action. It chooses experiments and implementation autonomously.
Optimize useful progress per wall-clock time, not active Agent count. Four idle-value routes are worse than one critical route; one busy Executor while three valuable independent routes wait is also failure.
When a deep route stalls, first add a fresh cognitive perspective or an independent sub-route. Do not seize the Executor's workspace.
Move between broad exploration and concentrated exploitation as evidence changes.
Assign one Executor to absorb complementary成果 and submit the combined result as a new Candidate.
You may inspect and reason, but do not edit the accepted world or submit production Candidates yourself.
```

The per-turn decision reminder is:

```text
1. Which independent high-value learning opportunities exist now?
2. Which valuable opportunity lacks an owner?
3. Which lines are producing discriminating evidence?
4. Which line needs another brain rather than replacement?
5. Which duplicate or weak line should be parked or retired?
6. Which complementary results need a designated Executor to absorb them?
7. What real dependency or authoritative evaluation is on the critical path?
```

- [ ] **Step 7: Parse Lab configuration and inject one runtime**

In `scientist/cli.py`, require this minimal evaluation shape and use assistant capacity defaults:

```python
evaluation_spec = spec.get("evaluation")
if not isinstance(evaluation_spec, dict) or not isinstance(evaluation_spec.get("command"), list):
    raise ValueError("spec.evaluation.command must be a non-empty argv list")
evaluation_command = tuple(str(item) for item in evaluation_spec["command"])
evaluation_timeout = int(evaluation_spec.get("timeout_seconds", 1800))
lab = LabRuntime(
    world.work, world.state_dir, world.scratch / "seat-worlds",
    evaluation_command, evaluation_timeout,
    assistant_config.executor_slots, assistant_config.cognitive_slots,
)
assistant = InWorldAssistant(world, ledger, lab, assistant_config)
session = ScientistSession(world=world, ledger=ledger, assistant=assistant, lab=lab)
```

There is exactly one `LabRuntime` per Scientist process. `ScientistSession` and `InWorldAssistant` share it; neither constructs its own stores.

- [ ] **Step 8: Separate normal conclusion from crash/restart behavior**

Remove unconditional `shutdown_pending()` from `run_episode`'s `finally`. On successful `deliver` or `abstain`, explicitly park active Executors and cancel remaining cognitive seats after salvaging their reports. On exception, SIGTERM, or supervisor cutoff, leave live child processes and manifests intact so the next SCI process can adopt them.

```python
def close_team_for_terminal_result(self) -> None:
    for seat in self.lab.seats.list():
        if seat.status != "active":
            continue
        if seat.role == "executor":
            self.assistant.park_executor(seat.seat_id, "SCI run concluded")
        elif seat.engagement_id is not None:
            self.assistant.cancel(seat.engagement_id)
```

Call this method only after `validate_conclusion` succeeds.

- [ ] **Step 9: Run control-loop and prompt regressions**

Run: `python -m pytest tests/scientist/test_model_native_tools.py tests/scientist/test_async_runtime.py tests/scientist/test_context_contract.py tests/scientist/test_collaboration.py tests/scientist/test_oneworld.py -q`

Expected: PASS; no test may require Reviewer acknowledgement before delivery.

- [ ] **Step 10: Commit the SCI control loop**

```bash
git add scientist/native_tools.py scientist/agent.py scientist/cli.py scientist/prompts/scientist.md scientist/prompts/scientist_code.md scientist/prompts/research_team.md tests/scientist/test_model_native_tools.py tests/scientist/test_async_runtime.py tests/scientist/test_context_contract.py tests/scientist/test_collaboration.py tests/scientist/test_oneworld.py
git commit -m "feat(scientist): manage a research portfolio"
```

---

### Task 10: Derived Portfolio UI

**Files:**
- Modify: `scientist/ui/reader.py`
- Modify: `scientist/ui/projector.py`
- Modify: `tests/scientist/ui/test_reader.py`
- Modify: `tests/scientist/ui/test_projector.py`
- Modify: `tests/scientist/ui/test_server.py`

**Interfaces:**
- Consumes persisted `seats/*.json`、`lab-events/events.jsonl`、`candidates/*.core.json`、`candidates/*.state.json`、`candidates/accepted.json`、`evaluations/eval-*.json`。
- Produces `read_run(run_dir: Path)["lab"]` and projected `portfolio` JSON; UI remains read-only and creates no secondary state.

- [ ] **Step 1: Write failing reader and opaque-label projection tests**

```python
# tests/scientist/ui/test_reader.py
def test_reader_loads_five_lab_primitives(run_dir):
    state = run_dir / "world" / ".scientist"
    (state / "seats").mkdir(parents=True)
    (state / "lab-events").mkdir()
    (state / "candidates").mkdir()
    (state / "evaluations").mkdir()
    (state / "seats" / "executor-1.json").write_text(
        '{"seat_id":"executor-1","role":"executor","status":"active","workspace":"/w1",'
        '"charter":"route A","engagement_id":"call-1","pid":1,"reason":"engaged"}', encoding="utf-8")
    (state / "lab-events" / "events.jsonl").write_text(
        '{"event_id":1,"author":"reviewer-1","audience":["public"],"label":"novel-label",'
        '"payload":{"x":1},"evidence_refs":[],"revision":null}\n', encoding="utf-8")
    (state / "candidates" / "accepted.json").write_text(
        '{"revision":"abc","candidate_id":null}', encoding="utf-8")

    data = read_run(run_dir)

    assert data["lab"]["accepted"]["revision"] == "abc"
    assert data["lab"]["seats"][0]["seat_id"] == "executor-1"
    assert data["lab"]["events"][0]["label"] == "novel-label"


# tests/scientist/ui/test_projector.py
def test_projector_preserves_unknown_event_labels_without_workflow_logic():
    projected = project_run({"lab": {"accepted": {"revision": "abc", "candidate_id": None},
                                     "seats": [], "candidates": [], "evaluations": [],
                                     "events": [{"event_id": 1, "author": "reviewer-1",
                                                 "audience": ["public"], "label": "novel-label",
                                                 "payload": {"x": 1}, "evidence_refs": [], "revision": None}]}})
    assert projected["portfolio"]["timeline"][0]["label"] == "novel-label"
    assert projected["portfolio"]["timeline"][0]["kind"] == "lab_event"
```

- [ ] **Step 2: Run tests to verify the new view fails**

Run: `python -m pytest tests/scientist/ui/test_reader.py tests/scientist/ui/test_projector.py -q`

Expected: FAIL because `lab` and `portfolio` are absent.

- [ ] **Step 3: Read primitive files directly without inferring new authority**

Add this reader helper and call it from `read_run` with the resolved `.scientist` state directory:

```python
def _json_rows(paths):
    rows = []
    for path in sorted(paths):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def read_lab(state_dir: Path) -> dict[str, object]:
    candidate_cores = {path.name.removesuffix(".core.json"): json.loads(path.read_text(encoding="utf-8"))
                       for path in (state_dir / "candidates").glob("*.core.json")}
    candidate_states = {path.name.removesuffix(".state.json"): json.loads(path.read_text(encoding="utf-8"))
                        for path in (state_dir / "candidates").glob("*.state.json")}
    accepted_path = state_dir / "candidates" / "accepted.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8")) if accepted_path.exists() else None
    events_path = state_dir / "lab-events" / "events.jsonl"
    events = ([json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
              if events_path.exists() else [])
    return {
        "accepted": accepted,
        "seats": _json_rows((state_dir / "seats").glob("*.json")),
        "events": events,
        "candidates": [{"candidate": candidate_cores[candidate_id],
                        "state": candidate_states.get(candidate_id)}
                       for candidate_id in sorted(candidate_cores)],
        "evaluations": _json_rows(path for path in (state_dir / "evaluations").glob("eval-*.json")
                                  if ".result." not in path.name),
    }
```

- [ ] **Step 4: Project a factual portfolio view**

Add this projector function and include its return value under `portfolio`:

```python
def project_portfolio(lab: dict[str, object]) -> dict[str, object]:
    return {
        "accepted_head": lab.get("accepted"),
        "seats": lab.get("seats", []),
        "candidates": lab.get("candidates", []),
        "evaluations": lab.get("evaluations", []),
        "timeline": [{**event, "kind": "lab_event"} for event in lab.get("events", [])],
        "counts": {
            "active_executors": sum(row.get("role") == "executor" and row.get("status") == "active"
                                    for row in lab.get("seats", [])),
            "active_cognitive": sum(row.get("role") != "executor" and row.get("status") == "active"
                                    for row in lab.get("seats", [])),
            "validating": sum(row.get("status") in {"queued", "running"}
                              for row in lab.get("evaluations", [])),
        },
    }
```

Do not map label names to workflow stages. The UI may display a label and payload but must treat every label uniformly.

- [ ] **Step 5: Run all UI tests**

Run: `python -m pytest tests/scientist/ui -q`

Expected: PASS.

- [ ] **Step 6: Commit the read-only projection**

```bash
git add scientist/ui/reader.py scientist/ui/projector.py tests/scientist/ui/test_reader.py tests/scientist/ui/test_projector.py tests/scientist/ui/test_server.py
git commit -m "feat(scientist): project the research portfolio"
```

---

### Task 11: End-to-End Migration, Example and Obsolete-Path Removal

**Files:**
- Create: `tests/scientist/test_portfolio_integration.py`
- Modify: `examples/omilrec_sci_opt/spec.json`
- Modify: `scientist/assistant_tools.py`
- Modify: `scientist/agent.py`
- Modify: `scientist/native_tools.py`
- Modify: `scientist/ledger.py`
- Modify: `docs/superpowers/specs/2026-09-04-scientist-multi-agent-portfolio-design.md`
- Test: all `tests/scientist/`

**Interfaces:**
- Consumes: complete runtime from Tasks 1–10.
- Produces: one tested production path with no current-world Executor mode, no synchronous Reviewer gate, no role-specific collaboration channel and no duplicate assistant-call authority.

- [ ] **Step 1: Write the failing portfolio acceptance scenario**

```python
# tests/scientist/test_portfolio_integration.py
import subprocess
import sys
import time

from scientist.lab import LabRuntime


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, "init")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "result.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "result.txt")
    git(path, "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD")


def wait_passed(lab, candidate_id):
    for _ in range(200):
        lab.tick()
        if lab.candidates.state(candidate_id).status == "passed":
            return
        time.sleep(0.01)
    raise AssertionError("candidate did not pass")


def test_two_lines_share_evidence_sync_and_resubmit_combined_work(tmp_path):
    live = tmp_path / "live"
    base = make_repo(live)
    evaluator = (
        "import json,os; json.dump({'outcome':'passed','metrics':{'score':2},'evidence':['gate']},"
        "open(os.environ['SCIENTIST_EVALUATION_RESULT'],'w'))"
    )
    lab = LabRuntime(live, tmp_path / "state", tmp_path / "worlds",
                     [sys.executable, "-c", evaluator], 5, 4, 2)
    seat_a = lab.seats.create("executor", None, "route A")
    seat_b = lab.seats.create("executor", None, "route B")
    world_a = lab.workspaces.fork(seat_a.seat_id, base)
    world_b = lab.workspaces.fork(seat_b.seat_id, base)
    lab.seats.set_workspace(seat_a.seat_id, str(world_a.path))
    lab.seats.set_workspace(seat_b.seat_id, str(world_b.path))

    lab.events.append("reviewer-1", [seat_a.seat_id, "sci"], "dissent",
                      {"risk": "aliasing"}, workspace=str(world_a.path), revision=base)
    assert lab.events.read(seat_a.seat_id)[-1].payload["risk"] == "aliasing"

    (world_a.path / "result.txt").write_text("candidate A\n", encoding="utf-8")
    git(world_a.path, "add", "result.txt")
    git(world_a.path, "commit", "-m", "candidate A")
    revision_a = lab.workspaces.checkpoint(seat_a.seat_id)
    candidate_a = lab.candidates.submit("executor", seat_a.seat_id, str(world_a.path),
                                        base, revision_a, "candidate A", ["event:1"])
    wait_passed(lab, candidate_a.candidate_id)
    accepted_a = lab.accept_candidate(candidate_a.candidate_id, base)

    accepted_events = [event for event in lab.events.read(seat_b.seat_id)
                       if event.label == "accepted_revision_advanced"]
    assert accepted_events[-1].payload["accepted_revision"] == accepted_a.revision
    assert lab.workspaces.sync_status(seat_b.seat_id, accepted_a.revision) == "behind"
    assert git(world_b.path, "rev-parse", "HEAD") == base
    lab.workspaces.sync_from_accepted(seat_b.seat_id, accepted_a.revision)
    assert git(world_b.path, "rev-parse", "HEAD") == accepted_a.revision

    (world_b.path / "combined.txt").write_text("absorbed A into route B\n", encoding="utf-8")
    git(world_b.path, "add", "combined.txt")
    git(world_b.path, "commit", "-m", "absorb A into B")
    revision_b = lab.workspaces.checkpoint(seat_b.seat_id)
    candidate_b = lab.candidates.submit("executor", seat_b.seat_id, str(world_b.path),
                                        accepted_a.revision, revision_b, "combined A+B", [candidate_a.candidate_id])
    wait_passed(lab, candidate_b.candidate_id)
    assert lab.candidates.state(candidate_b.candidate_id).status == "passed"
```

- [ ] **Step 2: Run the scenario to verify it fails**

Run: `python -m pytest tests/scientist/test_portfolio_integration.py -q`

Expected: FAIL at the first incomplete integration boundary; record the exact failure before changing production code.

- [ ] **Step 3: Add the minimum example configuration**

Add these keys to `examples/omilrec_sci_opt/spec.json`, preserving its existing command and benchmark arguments:

```json
{
  "assistant": {
    "executor_slots": 4,
    "cognitive_slots": 2
  },
  "evaluation": {
    "command": ["bash", "scripts/sl_eval_v100.sh", "--evtmax", "100"],
    "timeout_seconds": 1800
  }
}
```

Merge these members into the existing top-level and `assistant` objects; do not replace existing model, environment, gate or goal values.

- [ ] **Step 4: Delete obsolete runtime paths after the integration test uses their replacements**

Delete the following symbols and branches, then use `rg` to prove no production reference remains:

```text
scientist/assistant_tools.py: reviewer_heard_after
scientist/assistant_tools.py: Executor workspace="current" branch
scientist/native_tools.py: continue_engagement schema
scientist/native_tools.py: Executor workspace enum
scientist/agent.py: _LISTEN_REFUSAL_MAX
scientist/agent.py: Reviewer synchronous dispatch branch
scientist/agent.py: Reviewer-heard delivery refusal
scientist/agent.py: unconditional finally shutdown_pending
```

Run:

```bash
rg -n "reviewer_heard_after|_LISTEN_REFUSAL_MAX|continue_engagement|workspace.*current|shutdown_pending" scientist
```

Expected: no matches for the first four obsolete contracts. `shutdown_pending` may remain as an explicit hard-stop maintenance method, but `run_episode` must not call it from an unconditional `finally`.

- [ ] **Step 5: Keep `LocalLedger` compatibility-only**

Remove any new code path that writes cross-seat findings into `research-memory.jsonl` or role-specific files. `LocalLedger` may continue recording assistant call summaries and SCI research memory; all messages intended for another seat must call `LabEventStream.append`.

Add this persistence assertion:

```python
def test_cross_seat_event_has_one_authoritative_copy(tmp_path):
    from scientist.lab_events import LabEventStream
    from scientist.ledger import LocalLedger

    stream = LabEventStream(tmp_path / "state")
    ledger = LocalLedger(tmp_path / "state")
    event = stream.append("searcher-1", ["executor-1"], "finding", {"fact": "x"})
    matches = [row for row in stream.read("executor-1") if row.event_id == event.event_id]
    assert len(matches) == 1
    memory = ledger.memory_path.read_text(encoding="utf-8") if ledger.memory_path.exists() else ""
    assert "finding" not in memory
```

- [ ] **Step 6: Add the efficiency philosophy to the approved design**

Insert a `Research Portfolio / Efficiency Philosophy` subsection under the design's SCI scheduling section with these normative statements:

```text
SCI manages a portfolio rather than a serial task queue.
Parallelize independent learning; serialize true dependencies.
Maximize useful research progress per wall-clock time, not active seat count.
Executor charters describe durable research lines, not isolated micro-tasks.
Portfolio status is derived from the five primitives and does not introduce a sixth primitive.
Adding firepower creates another owned Workspace/sub-route; it never creates shared writers.
SCI may inspect and reason but production changes and result absorption remain Executor work.
```

- [ ] **Step 7: Run the end-to-end scenario and all Scientist tests**

Run: `python -m pytest tests/scientist/test_portfolio_integration.py -q`

Expected: PASS.

Run: `python -m pytest tests/scientist -q`

Expected: PASS, except only failures already recorded in Preflight. Any new failure must be fixed before proceeding.

- [ ] **Step 8: Run static repository checks**

```bash
python -m compileall -q scientist tests/scientist
git diff --check
git status --short
```

Expected: compile and whitespace checks exit 0. `git status --short` may still list pre-existing user changes; confirm the next staging command names only files in this task.

- [ ] **Step 9: Commit the migration**

```bash
git add tests/scientist/test_portfolio_integration.py examples/omilrec_sci_opt/spec.json scientist/assistant_tools.py scientist/agent.py scientist/native_tools.py scientist/ledger.py docs/superpowers/specs/2026-09-04-scientist-multi-agent-portfolio-design.md
git commit -m "feat(scientist): complete portfolio team migration"
```

---

## Final Verification

- [ ] Confirm all five primitives have one authoritative persistence location.
- [ ] Confirm every active Executor has a distinct workspace path and stable seat id.
- [ ] Confirm all cognitive roles launch asynchronously and none is a delivery gate.
- [ ] Confirm a cognitive experiment records an explicit stable source revision.
- [ ] Confirm only Executor CLI identity can create a production Candidate.
- [ ] Confirm at most one Evaluation is `running`.
- [ ] Confirm tool failure becomes `instrument_failure`, not research `failed`.
- [ ] Confirm accepted-head promotion is fast-forward-only and emits one public event.
- [ ] Confirm another Executor sees the event without being cancelled or auto-rebased.
- [ ] Confirm an Executor can explicitly sync, and conflicts remain in its owned workspace for that Executor to resolve.
- [ ] Confirm SCI restart does not kill a live Executor.
- [ ] Confirm successful final delivery parks remaining Executors and stops temporary cognitive seats.
- [ ] Confirm portfolio view is derived and no `ResearchLine` store or dependency engine exists.
- [ ] Confirm `python -m pytest tests/scientist -q`, `python -m compileall -q scientist tests/scientist`, and `git diff --check` pass relative to the recorded baseline.
