"""Factual lookup for the Proposer's research phase.

Two families of tools:
  - ``ResearchCommandRunner`` runs a bounded shell command in a sandboxed
    Apptainer boundary (the whole worktree mounted ``/work`` read-only, the
    editable paths overlaid read-write — plus repo read-only for git history,
    scratch writable, networked — measurement duty may need the calib DB).
  - ``ScientificMemoryTools`` dispatches the Proposer's memory operations to
    ``MemoryService``.

``ResearchTools`` is the façade the Proposer's runtime speaks to.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread

from simpleevo.research_state import ResearchState

from .research_agent import WorkingState
from .runtime import MountMap
from .child_processes import CHILD_PROCESSES
from scientist.research_files import PathBoundary, ResearchFiles
from scientist.research_skills import load_research_skill


@dataclass(frozen=True)
class ResearchToolSpec:
    """Prompt description owned by the research tool boundary."""

    action: str
    schema: str
    description: str


RESEARCH_TOOL_SPECS = (
    ResearchToolSpec(
        action="consult",
        schema=(
            '{"action":"consult","question":"...","context":"...",'
            '"read":"none|node|lab"}'
        ),
        description=(
            "Ask your assistant (问/辩/审). It can search the web and the "
            "literature, read code fast, and argue back; it never touches "
            "your world. read=node shows it the pristine world under study, "
            "read=lab your work in progress, read=none nothing. The return "
            "is a distilled BELIEF — adopting it is your judgment. For 辩 "
            "state your hypothesis and demand refutation, not agreement."
        ),
    ),
    ResearchToolSpec(
        action="work",
        schema=(
            '{"action":"work","instruction":"...","mode":"continue|fresh",'
            '"budget_minutes":30}'
        ),
        description=(
            "Your assistant executes in your laboratory (做) — the default "
            "for implementation, refactors, and measurement campaigns. "
            "continue works in your main world (your edits and its edits "
            "share it); fresh runs a throwaway side world. Brief it like a "
            "capable junior: mechanism, files, constraints, what to "
            "self-measure. The harness snapshots the world after each call; "
            "you get a distillation and its self-measured numbers — your "
            "own verification remains your responsibility."
        ),
    ),
    ResearchToolSpec(
        action="update_research_state",
        schema=(
            '{"action":"update_research_state",'
            '"working_model":"your current scientific understanding",'
            '"evidence_refs":[],'
            '"evidence":[{"claim":"...","how":"verified how",'
            '"numbers":{},"source":"experiment:...|source:...|assistant:...",'
            '"status":"belief"}],'
            '"experiment_log":[{"intent":"...","sha":"...","numbers":{},'
            '"verdict":"..."}],'
            '"deliverables":[{"world_sha":"...","material_difference":"..."}],'
            '"conclusion":{"type":"delivered|empty|cut_off",'
            '"exhaustion":"...","open_questions":[...]}}'
        ),
        description=(
            "Upsert your lease's ONE evolving research state (six blocks), "
            "written to the ledger immediately; revision increments each "
            "write. Evidence you author is belief; verified is "
            "harness-awarded, never yours to claim. Revise after every work "
            "cycle and before any conclusion."
        ),
    ),
    ResearchToolSpec(
        action="read_file",
        schema=(
            '{"action":"read_file","path":"/work/...",'
            '"offset":1,"limit":400}'
        ),
        description=(
            "Read one file with line numbers (path absolute under /work, "
            "/repo, or /scratch; offset 1-based; limit default 400, max "
            "2000). Reach for read_file before shelling out cat/sed/head."
        ),
    ),
    ResearchToolSpec(
        action="grep_files",
        schema=(
            '{"action":"grep_files","pattern":"...","path":"/work",'
            '"glob":"*.cc","context":2,"max_matches":50}'
        ),
        description=(
            "Regex content search under a directory or one file (glob "
            "narrows; context adds surrounding lines; max_matches default "
            "50). Returns path:line:text rows. Reach for grep_files before "
            "shelling out grep/rg."
        ),
    ),
    ResearchToolSpec(
        action="glob_files",
        schema=(
            '{"action":"glob_files","pattern":"**/*.py","path":"/work",'
            '"limit":200}'
        ),
        description=(
            "List file paths matching a glob under a root (default /work), "
            "capped at limit (default 200). Reach for glob_files before "
            "shelling out find/ls."
        ),
    ),
    ResearchToolSpec(
        action="run_research_command",
        schema=(
            '{"action":"run_research_command","command":"...",'
            '"cwd":"work|scratch","workdir":"/work/sub/dir"}'
        ),
        description=(
            "Bounded shell command in your lab — for what the dedicated "
            "tools cannot do: compiling, running, measuring, git. workdir "
            "(absolute, under /work or /scratch) is remembered across "
            "calls; cwd is the coarse work|scratch spelling. Git history is "
            "readable via /repo; you cannot commit."
        ),
    ),
    ResearchToolSpec(
        action="write_scratch_file",
        schema=(
            '{"action":"write_scratch_file","path":"/scratch/...",'
            '"content":"..."}'
        ),
        description=(
            "Write a file under /scratch with exactly this content "
            "(heredoc quoting in a shell command corrupts code — use this "
            "for scratch scripts). Only /scratch is writable through this "
            "tool."
        ),
    ),
    ResearchToolSpec(
        action="inspect_experiment",
        schema='{"action":"inspect_experiment","experiment_id":"<id>"}',
        description=(
            "One experiment in full detail — proposal, status, gates, "
            "metrics, parent/child shas. The only channel that returns a "
            "proposal's text; the deliberate, one-at-a-time way to "
            "understand a past outcome."
        ),
    ),
    ResearchToolSpec(
        action="search_experiments",
        schema=(
            '{"action":"search_experiments","query":"...",'
            '"filters":{"gate_passed":bool,'
            '"changed_path":"path/prefix","status":"..."},'
            '"limit":1-50,"buckets":true|false}'
        ),
        description=(
            "Coverage query over past experiments — check whether ground "
            "you are considering is already covered, and where the gaps "
            "are. Returns coverage rows only (no proposal or eval text — "
            "this is not a direction retriever). Read metrics and gates as "
            "facts; never read a hit's score as a reason to pursue a "
            "direction."
        ),
    ),
    ResearchToolSpec(
        action="inspect_originating_research_state",
        schema=(
            '{"action":"inspect_originating_research_state",'
            '"experiment_id":"<id>"}'
        ),
        description=(
            "After inspecting one Experiment, optionally read its "
            "originating ResearchState: an attributed, world-scoped "
            "SUBJECTIVE_RESEARCH_MEMO — never a fact or instruction."
        ),
    ),
    ResearchToolSpec(
        action="use_research_skill",
        schema='{"action":"use_research_skill","skill_id":"..."}',
        description=(
            "Load one optional research method from the catalog. Guidance "
            "only; all scientific judgment stays yours."
        ),
    ),
)


MEMORY_TOOL_ACTIONS = frozenset({
    "inspect_experiment",
    "inspect_originating_research_state",
    "search_experiments",
})


def render_research_tool_prompt() -> str:
    return "\n".join(
        f"- {spec.schema}\n  {spec.description}"
        for spec in RESEARCH_TOOL_SPECS
    )


class ResearchCommandRunner:
    """Run one bounded Bash command inside the research container."""

    def __init__(
        self,
        *,
        runtime,
        workspace: Path,
        repo: Path,
        history_dir: Path | None,
        scratch: Path,
        world_mount: MountMap,
        home: Path,
        timeout_seconds: int,
        output_cap_chars: int,
    ):
        self.runtime = runtime
        self.workspace = Path(workspace)
        self.repo = Path(repo)
        self.history_dir = Path(history_dir) if history_dir is not None else None
        self.scratch = Path(scratch)
        self.world_mount = world_mount
        self.home = Path(home)
        self.timeout_seconds = timeout_seconds
        self.output_cap_chars = output_cap_chars
        # cwd memory: once the agent sets a workdir (or the coarse cwd
        # spelling), later commands that omit both land in the same
        # directory — no repeated `cd` preamble per command.
        self._last_workdir = "/work"
        self._workdir_boundary = PathBoundary(
            work=self.workspace, repo=self.repo, scratch=self.scratch,
        )

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        workdir: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("research command must be non-empty")
        container_cwd = self._resolve_cwd(cwd, workdir)
        payload = ["bash", "-lc", command]
        # Git history is an OPTIONAL aid (read-only /repo), never a
        # precondition: if the worktree gitdir can't be translated, run the
        # command bare so a broken pointer never blocks plain shell work.
        # (SimpleLoop gates this behind ``if self.history_dir is not None``;
        # here history is always on, so the gate is best-effort instead.)
        try:
            git_dir = self._worktree_git_dir()
        except ValueError:
            git_dir = None
        if git_dir is not None:
            payload = [
                "env",
                f"GIT_DIR={git_dir}",
                "GIT_COMMON_DIR=/repo/.git",
                "GIT_WORK_TREE=/work",
                *payload,
            ]
        extra_binds = [
            f"{self.repo.resolve()}:/repo:ro",
            f"{self.scratch.resolve()}:/scratch:rw",
        ]
        # NOTE: /history.jsonl and /rounds are intentionally NOT mounted into
        # the shell sandbox. The experiment ledger is the Scientist's coverage
        # map, not an idea mine: bulk shell access to every past proposal text
        # + eval would make history-mining trivial (the charter forbids it, but
        # prose cannot restrain a grep). History access is routed through the
        # framed memory tools (inspect_experiment / search_experiments), which
        # return coverage or single-experiment detail on demand. The
        # ``history_dir`` param is retained for call-site compatibility.
        argv = self.runtime.exec_argv(
            payload,
            cwd=self.workspace,
            mounts=self.world_mount,
            home=self.home,
            extra_binds=extra_binds,
            work_cwd=container_cwd,
        )
        process = subprocess.Popen(
            argv,
            cwd=str(self.runtime.run_dir),
            env=self.runtime.research_subprocess_env(
                home=self.runtime.executor_home
            ),
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        CHILD_PROCESSES.register(process.pid)
        timed_out = False
        timeout = (
            self.timeout_seconds
            if timeout_seconds is None
            else min(self.timeout_seconds, timeout_seconds)
        )
        stdout_result: dict = {}
        stderr_result: dict = {}
        readers = [
            Thread(
                target=_drain_bounded,
                args=(process.stdout, self.output_cap_chars, stdout_result),
            ),
            Thread(
                target=_drain_bounded,
                args=(process.stderr, self.output_cap_chars, stderr_result),
            ),
        ]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process.pid)
            process.wait()
            returncode = None
        finally:
            if not timed_out:
                _kill_process_group(process.pid)
            for reader in readers:
                reader.join()
            CHILD_PROCESSES.unregister(process.pid)
        stdout = stdout_result.get("text", "")
        stderr = stderr_result.get("text", "")
        output = stdout
        if stderr:
            output += "\n[stderr]\n" + stderr
        truncated = (
            stdout_result.get("truncated", False)
            or stderr_result.get("truncated", False)
            or len(output) > self.output_cap_chars
        )
        output = output[:self.output_cap_chars]
        return {
            "ok": not timed_out and returncode == 0,
            "returncode": returncode,
            "timed_out": timed_out,
            "truncated": truncated,
            "output": output,
        }

    def _resolve_cwd(self, cwd: str | None, workdir: str | None) -> str:
        """Pick the container cwd for one command, updating the memory.

        ``workdir`` (absolute, under /work or /scratch) wins when both are
        given; ``cwd`` is the coarse work|scratch spelling; with neither,
        the last choice persists.
        """
        if workdir is not None:
            if workdir == "/repo" or workdir.startswith("/repo/"):
                raise ValueError(
                    "workdir must be under /work or /scratch, not /repo"
                )
            host = self._workdir_boundary.resolve(workdir)
            if not host.is_dir():
                raise ValueError(f"workdir does not exist: {workdir!r}")
            self._last_workdir = workdir
            return workdir
        if cwd is not None:
            if cwd not in {"work", "scratch"}:
                raise ValueError(
                    "research cwd must be 'work' or 'scratch'"
                )
            self._last_workdir = "/scratch" if cwd == "scratch" else "/work"
        return self._last_workdir

    def _worktree_git_dir(self) -> str:
        git_file = self.workspace / ".git"
        try:
            line = git_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                f"research workspace has no worktree metadata: {git_file}"
            ) from exc
        if not line.startswith("gitdir: "):
            raise ValueError(f"invalid worktree metadata: {git_file}")
        git_dir = Path(line.removeprefix("gitdir: "))
        if not git_dir.is_absolute():
            git_dir = git_file.parent / git_dir
        admin_root = (self.repo / ".git" / "worktrees").resolve()
        try:
            relative = git_dir.resolve().relative_to(admin_root)
        except ValueError as exc:
            raise ValueError(
                "research worktree metadata points outside the run repository"
            ) from exc
        if len(relative.parts) != 1:
            raise ValueError("invalid research worktree admin path")
        return f"/repo/.git/worktrees/{relative.as_posix()}"


def _drain_bounded(stream, cap: int, result: dict) -> None:
    chunks: list[str] = []
    kept = 0
    truncated = False
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        room = cap - kept
        if room > 0:
            retained = chunk[:room]
            chunks.append(retained)
            kept += len(retained)
        if len(chunk) > max(room, 0):
            truncated = True
    result.update(text="".join(chunks), truncated=truncated)


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class ResearchTools:
    """Dispatch the Proposer's non-terminal research actions.

    Command execution is delegated to ``ResearchCommandRunner``. All memory
    lookups (findings, experiments, episodes) go through ``MemoryService``.
    """

    def __init__(
        self,
        *,
        runtime,
        workspace: Path,
        repo: Path,
        history_dir: Path | None,
        scratch: Path,
        world_mount: MountMap,
        home: Path,
        memory_service,
        command_timeout_seconds: int,
        command_output_cap_chars: int,
        current_round: int | None = None,
        node_id: str | None = None,
        episode_id: str | None = None,
        inherited_research_states: dict[str, str] | None = None,
        hands=None,
        db_path: Path | None = None,
        lease_id: str | None = None,
    ):
        self.memory = memory_service
        self.command_timeout_seconds = command_timeout_seconds
        self.node_id = node_id
        self.episode_id = episode_id
        self.inherited_research_states = inherited_research_states or {}
        # The seat's claude assistant (consult/work) and the narrow write
        # path for incremental state registration (科学家完整研究制 §2.2/2.3).
        self.hands = hands
        self.db_path = db_path
        self.lease_id = lease_id
        self.files = ResearchFiles(
            work=workspace,
            repo=repo,
            scratch=scratch,
            cap_chars=command_output_cap_chars,
        )
        self.command_runner = ResearchCommandRunner(
            runtime=runtime,
            workspace=workspace,
            repo=repo,
            history_dir=history_dir,
            scratch=scratch,
            world_mount=world_mount,
            home=home,
            timeout_seconds=command_timeout_seconds,
            output_cap_chars=command_output_cap_chars,
        )

    def execute(
        self,
        action: dict,
        *,
        deadline: float,
        working_state: WorkingState | None = None,
    ) -> dict:
        """Execute one action; never raises — usage and I/O failures come
        back as ``{"ok": False, "error": ...}`` observations so a batch
        continues past a bad item."""
        name = action["action"]
        try:
            if name == "use_research_skill":
                return {
                    "ok": True,
                    "skill_id": action["skill_id"],
                    "content": load_research_skill(action["skill_id"]),
                }
            if name == "update_research_state":
                return self._update_research_state(action, working_state)
            if name == "register_research_state":
                # Legacy alias from the proposal era; same handler.
                return self._update_research_state(action, working_state)
            if name in {"consult", "work"}:
                if self.hands is None:
                    return {
                        "ok": False,
                        "error": "assistant hands not configured",
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"ok": False, "error": "proposer deadline exceeded"}
                if name == "consult":
                    return {
                        "ok": True,
                        "result": self.hands.consult(
                            action["question"],
                            context=action.get("context", ""),
                            read=action.get("read", "none"),
                        ),
                    }
                return {
                    "ok": True,
                    "result": self.hands.work(
                        action["instruction"],
                        mode=action.get("mode", "continue"),
                        budget_minutes=action.get("budget_minutes"),
                    ),
                }
            if name == "run_research_command":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"ok": False, "error": "proposer deadline exceeded"}
                return self.command_runner.run(
                    action["command"],
                    cwd=action.get("cwd"),
                    workdir=action.get("workdir"),
                    timeout_seconds=min(
                        self.command_timeout_seconds, remaining,
                    ),
                )
            if name == "read_file":
                return self.files.read_file(
                    action["path"],
                    offset=action.get("offset", 1),
                    limit=action.get("limit", 400),
                )
            if name == "grep_files":
                return self.files.grep_files(
                    action["pattern"],
                    path=action.get("path", "/work"),
                    glob=action.get("glob"),
                    context=action.get("context", 0),
                    max_matches=action.get("max_matches", 50),
                )
            if name == "glob_files":
                return self.files.glob_files(
                    action["pattern"],
                    path=action.get("path", "/work"),
                    limit=action.get("limit", 200),
                )
            if name == "write_scratch_file":
                return self.files.write_scratch_file(
                    action["path"],
                    content=action["content"],
                )
            if name == "inspect_experiment":
                return {
                    "ok": True,
                    "result": self.memory.inspect_experiment(action["experiment_id"]),
                }
            if name == "inspect_originating_research_state":
                state = self._require_cognitive_state(working_state)
                experiment_id = action["experiment_id"]
                if experiment_id not in state.inspected_experiment_ids:
                    raise ValueError(
                        "inspect experiment before requesting its research memo: "
                        f"{experiment_id}"
                    )
                return {
                    "ok": True,
                    "result": self.memory.inspect_originating_research_state(
                        experiment_id,
                    ),
                }
            if name == "search_experiments":
                return {
                    "ok": True,
                    "result": self.memory.search_experiments(
                        query=action["query"],
                        filters=action.get("filters") or None,
                        limit=action.get("limit", 10),
                        buckets=bool(action.get("buckets", True)),
                    ),
                }
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unsupported research action: {name}"}

    def _require_cognitive_state(
        self, working_state: WorkingState | None,
    ) -> WorkingState:
        if working_state is None:
            raise ValueError("cognitive action requires round-local WorkingState")
        if not self.node_id or not self.episode_id:
            raise ValueError("cognitive action requires node_id and episode_id")
        return working_state

    def _update_research_state(
        self, action: dict, working_state: WorkingState | None,
    ) -> dict:
        """Upsert the lease's evolving six-block research state.

        The write is incremental and immediate (lease_writer): a crash
        mid-lease no longer evaporates the investigation, and every work
        cycle's understanding is on file at the moment it forms.  The
        in-session copy stays for the round's own guards and telemetry.
        """
        state = self._require_cognitive_state(working_state)
        working_model = str(action.get("working_model") or "").strip()
        if not working_model:
            raise ValueError("working_model must be non-empty")
        evidence_refs = tuple(action.get("evidence_refs", ()))
        for evidence_ref in evidence_refs:
            if evidence_ref.startswith("source:"):
                if "__source_examined__" not in state.session_evidence:
                    raise ValueError("source evidence requires source inspection")
            elif evidence_ref not in state.session_evidence:
                raise ValueError(f"unseen evidence reference: {evidence_ref}")
        # Six-block payload.  evidence entries carry status belief|verified;
        # verified is only ever set by the harness at graduation — a seat
        # marking its own claim verified is a protocol violation.
        evidence = _as_entry_list(action.get("evidence"))
        for entry in evidence:
            if entry.get("status", "belief") == "verified":
                raise ValueError(
                    "evidence.status=verified is harness-awarded at "
                    "graduation; your own entries are belief"
                )
        experiment_log = _as_entry_list(action.get("experiment_log"))
        deliverables = _as_entry_list(action.get("deliverables"))
        conclusion = action.get("conclusion")
        if conclusion is not None and not isinstance(conclusion, dict):
            raise ValueError("conclusion must be an object")

        research_state_id = f"rs-{self.episode_id}-head"
        record = ResearchState(
            research_state_id=research_state_id,
            node_id=self.node_id,
            episode_id=self.episode_id,
            derived_from_research_state_id=None,
            working_model=working_model,
            evidence_refs=evidence_refs,
            created_at=time.time(),
            evidence=tuple(evidence),
            experiment_log=tuple(experiment_log),
            deliverables=tuple(deliverables),
            conclusion=conclusion,
            lease_id=self.lease_id,
        )
        state.research_states[research_state_id] = record
        if self.db_path is not None and self.lease_id:
            from simpleevo.db.lease_writer import upsert_lease_research_state

            revision = upsert_lease_research_state(
                self.db_path,
                lease_id=self.lease_id,
                episode_id=self.episode_id,
                node_id=self.node_id,
                working_model=working_model,
                evidence=evidence,
                experiment_log=experiment_log,
                deliverables=deliverables,
                conclusion=conclusion,
                evidence_refs=list(evidence_refs),
            )
            return {
                "ok": True,
                "research_state_id": research_state_id,
                "revision": revision,
            }
        return {"ok": True, "research_state_id": research_state_id}


def _as_entry_list(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of entries")
    out = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
    return out
