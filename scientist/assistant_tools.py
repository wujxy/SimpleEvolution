"""One subprocess runtime for research-team engagements.

An engagement is a DIRECTORY, not a function call: its lifecycle is
defined by which files exist under ``.scientist/assistant/<id>/`` —

    manifest.json   written at launch  (role, box, workspace, argv, ...)
    prompt.txt      written at launch
    raw.txt         grown in flight by the seat's own stdout descriptor
    proc.pid        in flight
    digest.json     written at collection — the report; the status is a
                    FIELD (done / unparsed / timeout-salvaged /
                    crash-salvaged / cancelled / failed), never a string
                    match
    read.marker     touched when the PI's loop has delivered the report

The class is a set of nearly stateless verbs over that convention:
``launch`` (fire), ``sweep`` (collect what finished or blew its box —
the same code path at the loop's turn top, inside ``wait``, at episode
exit, and at startup recovery), ``wait_for_seats``, ``continue_engagement``
(resume a finished Executor's claude session in its existing workspace).
The in-memory ``_procs`` cache is an optimization for returncode reads,
never the source of truth; the process table and the directories are.

Seat discipline is enforced by the workspace, not by tool denial: every
seat gets the full tool face; cognitive seats (proposer, challenger,
searcher-with-lab) run inside a disposable fork of the current world
whose data directories are symlinks into the read-only originals —
prototyping is free, nothing reaches the live tree, and the digest is
the only channel home.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .collaboration import (ROLE_NAMES, build_collaboration_prompt,
                            build_continuation_prompt)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Blinded cognitive seats (no Bash/Write) burned whole engagements fighting
# the permission wall instead of proposing: the briefs demand evidence
# ("establish with evidence, prototype"), and npz data cannot be read with
# Read/Grep. Every seat therefore gets the SAME full tool face — what
# separates a cognitive seat from an executor is the workspace (disposable
# fork vs the live tree) and the digest contract, nothing else.
_SEAT_TOOLS = "Read,Grep,Glob,Edit,Write,Bash,WebSearch,WebFetch,Task"

def _cap_words(text: str, cap: int) -> tuple[str, bool]:
    words = text.split()
    if len(words) <= cap:
        return text, False
    return " ".join(words[:cap]) + " …[truncated at cap]", True


def _parse_tail(text: str) -> dict | None:
    for raw in reversed(_JSON_FENCE_RE.findall(text or "")):
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _decode_stream(stdout: str) -> tuple[str, object]:
    """Decode a claude ``--output-format stream-json`` line stream.

    The final ``result`` event carries the finished text and usage."""
    text = stdout
    usage: object = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            result = event.get("result")
            if isinstance(result, str):
                text = result
            usage = event.get("usage")
    return text, usage


def _session_id_from_raw(raw: str) -> str:
    """First non-empty ``session_id`` in a seat's stream-json transcript.

    The ``system/init`` line precedes any work, so the id survives a
    time-box kill or a crash — which is exactly when continuation is
    most wanted. Empty when the stream shape changes: continuation then
    degrades to a clear rejection, never a wrong resume."""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            sid = event.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
    return ""


def _box_from_action(action: dict, default_seconds: int,
                     max_minutes: int) -> int:
    """Per-engagement time box, honoring the ``timeout_minutes`` field
    every role's PI-facing schema already exposes. (It used to be read
    for executors only — the PI could buy 60 minutes and still watch a
    proposer die at the 15-minute default: a wiring lie, now one shared
    path.) The ceiling is per-config, so a big-project spec can raise it
    without touching code."""
    try:
        minutes = int(action["timeout_minutes"])
    except (KeyError, TypeError, ValueError):
        return default_seconds
    return max(1, min(minutes, max_minutes)) * 60


# The one fork implementation lives in mkexp (kept self-contained so the
# seat kit can copy that file alone); the speculative-executor prebuild
# and the kit share it. Inheritance across a fork is within-run by
# construction — only the PI's private record (.scientist) stays home.
from .mkexp import fork_world as _fork_world


def _partial_report(raw: str) -> tuple[str, int]:
    """Last substantive assistant text + tool-call count from a partial
    (killed or crashed) stream-json transcript — the salvage payload when
    a time box expires mid-flight."""
    texts = []
    tools = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        for chunk in (event.get("message") or {}).get("content") or []:
            if not isinstance(chunk, dict):
                continue
            if chunk.get("type") == "tool_use":
                tools += 1
            elif chunk.get("type") == "text" and str(
                    chunk.get("text") or "").strip():
                texts.append(str(chunk["text"]).strip())
    if not texts:
        return "", tools
    # if the last utterance is a stub ("let me check X" right before the
    # kill), the substantive report is the one before it
    if len(texts[-1]) >= 200 or len(texts) == 1:
        return texts[-1], tools
    return " ".join(texts[-2:]), tools


@dataclass
class AssistantConfig:
    command: str = "claude"
    model: str | None = None
    effort: str | None = None
    node_world: Path | None = None      # pristine copy (harness-provided)
    env: dict | None = None             # merged over os.environ
    distill_word_cap: int = 1200
    consult_timeout_seconds: int = 3600
    cognitive_timeout_seconds: int = 10800
    work_default_minutes: int = 120
    seat_timeout_max_minutes: int = 480
    goal: str = ""
    gate_block: str = ""

    @classmethod
    def from_spec(cls, spec: dict) -> "AssistantConfig":
        assistant = dict(spec.get("assistant") or {})
        node_world = assistant.get("node_world")
        return cls(
            command=str(assistant.get("command") or "claude"),
            model=assistant.get("model") or None,
            effort=assistant.get("effort") or None,
            node_world=Path(node_world) if node_world else None,
            env=assistant.get("env") or None,
            distill_word_cap=int(
                (spec.get("budget") or {}).get(
                    "distill_word_cap", 1200)),
            seat_timeout_max_minutes=int(
                (spec.get("budget") or {}).get(
                    "seat_timeout_max_minutes", 480)),
            consult_timeout_seconds=int(
                (spec.get("budget") or {}).get(
                    "consult_timeout_seconds", 3600)),
            cognitive_timeout_seconds=int(
                (spec.get("budget") or {}).get(
                    "cognitive_timeout_seconds", 10800)),
            work_default_minutes=int(
                (spec.get("budget") or {}).get(
                    "work_default_minutes", 120)),
            goal=str(spec.get("goal") or ""),
            gate_block=str(spec.get("gate_block") or ""),
        )


class InWorldAssistant:
    """Claude subprocess runtime behind the research roles. The seat's
    unit of existence is its directory (see module docstring); this class
    holds no lifecycle state beyond the id counter and a Popen cache."""

    _CALL_SEQ_RE = re.compile(r"-(\d{3})$")

    # forks are kept for continuation but not forever: a 7-day run with
    # dozens of engagements must not accumulate workspaces without bound
    _FORK_KEEP_MAX = 24
    # never GC a fork younger than this — a batch of seats may be running
    # in freshly created forks (boxes are capped at 480 min)
    _FORK_MIN_AGE_SECONDS = 8.5 * 3600   # must exceed the max box (480 min)

    def __init__(
        self,
        *,
        world,
        config: AssistantConfig,
        ledger,
        episode_id: str = "ep",
        has_benchmark: bool = True,
    ):
        self.world = world
        self.config = config
        self.ledger = ledger
        self.episode_id = episode_id
        self.has_benchmark = has_benchmark
        self._counter = 0
        self._lock = threading.Lock()
        # returncode-read cache only — the directories are the truth
        self._procs: dict[str, subprocess.Popen] = {}
        self._reconcile()

    # -- plumbing ----------------------------------------------------------

    def _base(self) -> Path:
        """Seat homes live in the scratch area (Seat ≠ World): runtime
        detail, not record. Container: /scratch/<id>; standalone: the
        run-level seats/ sibling of the world."""
        return self.world.scratch

    def _legacy_base(self) -> Path:
        """Pre-redesign seat homes (.scientist/assistant) — kept readable
        so an old run resumed on this code still finds its seats."""
        return self.world.state_dir / "assistant"

    def _seat_roots(self) -> list[Path]:
        return [self._base(), self._legacy_base()]

    def _world_runtime(self) -> tuple[Path, Path]:
        """One run, one claude runtime — fresh and run-scoped.

        First principle (2026-09-01 ruling): a run's world opens brand
        new; nothing outside it is visible; the channel is exactly what
        the spec pins; nothing of the user's is borrowed for
        compatibility. Isolation is run-by-run — inside the run,
        inheritance is free. Concretely: the claude CLI applies the
        user's ``~/.claude/settings.json`` env block OVER the subprocess
        environment (every standalone-run seat silently answered glm-5.3
        on the user's coding plan; ``--settings`` does not override it),
        and reads skills/plugins/projects from the same tree. So the
        run carries its own ``.claude`` (settings = the spec's env,
        sessions land here, resume works within the run) and its own
        ``home`` (git identity belongs to the run, not the user).

        Placement — the scratch root, beside the seat homes: the claude
        runtime is BODY (three-zone design), and bodies live in the
        scratch mount, never in the research face. Inside the git tree
        it would be untracked noise at best and, at worst, food for a
        colleague's legitimate ``git clean``/``stash -u`` — the exact
        structural hole that once ate a run's whole record."""
        config = self.world.scratch / ".claude"
        home = self.world.scratch / "home"
        config.mkdir(parents=True, exist_ok=True)
        home.mkdir(parents=True, exist_ok=True)
        settings = config / "settings.json"
        payload = json.dumps({"env": self.config.env or {}}, indent=2)
        if not settings.exists() or \
                settings.read_text(encoding="utf-8") != payload:
            settings.write_text(payload, encoding="utf-8")
        settings.chmod(0o600)      # carries the run's credentials
        gitconfig = home / ".gitconfig"
        if not gitconfig.exists():
            gitconfig.write_text(
                "[user]\n"
                f"\tname = {self.episode_id}\n"
                f"\temail = {self.episode_id}@run.invalid\n",
                encoding="utf-8",
            )
        return config, home

    def _dir_of(self, call_id: str) -> Path:
        new = self._base() / call_id
        if new.exists():
            return new
        legacy = self._legacy_base() / call_id
        return legacy if legacy.exists() else new

    def _next_call_id(self, kind: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{kind}-{self.episode_id}-{self._counter:03d}"

    def _raw_dir(self, call_id: str) -> Path:
        d = self._dir_of(call_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist_raw(self, call_id: str, prompt: str,
                     digest: dict) -> None:
        d = self._raw_dir(call_id)
        (d / "prompt.txt").write_text(prompt, encoding="utf-8")
        # raw.txt is grown in place by the seat process's own file
        # descriptor — rewriting megabytes back into it buys nothing
        (d / "digest.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def _note(self, call_id: str, kind: str, digest: str,
              usage: object) -> None:
        with self._lock:
            self.ledger.note_assistant_call({
                "call_id": call_id,
                "episode_id": self.episode_id,
                "kind": kind,
                "question_digest": digest[:400],
                "usage": usage if isinstance(usage, dict) else None,
            })

    def _command_payload(self, allowed_tools: str,
                         resume_session: str | None = None) -> list[str]:
        payload = [
            self.config.command, "-p",
        ]
        if resume_session:
            payload += ["--resume", resume_session]
        payload += [
            "--input-format", "text",
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", allowed_tools,
        ]
        if self.config.model:
            payload += ["--model", self.config.model]
        if self.config.effort:
            payload += ["--effort", self.config.effort]
        return payload

    def reviewer_heard_after(self, moment: float) -> bool:
        """True if some Reviewer engagement finalized after ``moment``.

        The listen-before-deliver door check: a look-back counts when it
        finished after the last change to the world it is judging. A
        salvaged report counts too — a partial reading was still heard.
        ``finished_at`` is the recorded field; mtime is the fallback for
        records written before the field existed.
        """
        digests = []
        for root in self._seat_roots():
            try:
                digests += list(root.glob("reviewer-*/digest.json"))
            except OSError:
                continue
        for path in digests:
            try:
                finished = json.loads(
                    path.read_text(encoding="utf-8")).get("finished_at")
                when = float(finished) if finished is not None \
                    else path.stat().st_mtime
            except (OSError, ValueError, json.JSONDecodeError):
                try:
                    when = path.stat().st_mtime
                except OSError:
                    continue
            if when > moment:
                return True
        return False

    def _write_experiment_kit(self, seat_scratch: Path) -> None:
        """The make-experiment script: a cognitive seat's license to
        create a disposable world at the moment it needs one — resources
        by behavior, not by role. The script backs onto a COPY of
        scientist.mkexp placed in the seat's scratch (self-contained,
        stdlib-only): a seat must not be handed the repository — its
        harness prompts included — just to fork a world. Each experiment
        is stamped with the source world's git baseline so the Scientist
        can read a seat's experiment against the state it forked from."""
        support = seat_scratch / "kit"
        support.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            Path(__file__).resolve().parent / "mkexp.py",
            support / "mkexp.py")
        script = seat_scratch / "make-experiment"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "# make-experiment — create a disposable copy of the live\n"
            "# world to modify and test in. Reading the live world stays\n"
            "# reading; a world-changing test lives in the copy. Creates\n"
            "# exp-NNN/ beside this script and prints its path.\n"
            "set -euo pipefail\n"
            "N=0\n"
            "for d in exp-[0-9][0-9][0-9]; do\n"
            "  [ -d \"$d\" ] && N=$((N+1))\n"
            "done\n"
            "TARGET=$(printf 'exp-%03d' $((N+1)))\n"
            f"{sys.executable} \"$(dirname \"$0\")/kit/mkexp.py\" \\\n"
            f"    --source {self.world.work} --dest \"$TARGET\"\n"
            "echo \"experiment world: $TARGET/"
            " (baseline in $TARGET/EXPERIMENT_BASELINE)\"\n",
            encoding="utf-8",
        )
        script.chmod(0o755)

    def _gc_forks(self) -> None:
        """Trim kept experiment worlds to the most recent _FORK_KEEP_MAX.
        Only worlds older than _FORK_MIN_AGE_SECONDS are eligible, so a
        sibling seat running in a batch (box capped at 480 min) is never
        touched. Covers both layouts: seats/<id>/world (redesign) and the
        pre-redesign scratch/fresh-* of an old run resumed here."""
        now = time.time()
        forks: list[Path] = []
        for root in self._seat_roots():
            try:
                forks += [p for p in root.glob("*/world") if p.is_dir()]
                forks += [p for p in root.glob("fresh-*") if p.is_dir()]
            except OSError:
                continue
        forks.sort(key=lambda p: p.stat().st_mtime)
        for stale in forks[:max(0, len(forks) - self._FORK_KEEP_MAX)]:
            try:
                if now - stale.stat().st_mtime < self._FORK_MIN_AGE_SECONDS:
                    continue
            except OSError:
                continue
            shutil.rmtree(stale, ignore_errors=True)

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        """Terminate the seat's whole process group and reap it.

        SIGTERM first — a still-live stream may flush a final chunk the
        salvage pass can harvest — then SIGKILL after a grace period."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (OSError, ProcessLookupError):
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=10)
                return
            except (OSError, subprocess.TimeoutExpired):
                continue

    @staticmethod
    def _kill_pid(pid: int) -> None:
        """Kill a seat by pid when no Popen is cached (startup recovery):
        guarded by the cmdline check so a recycled pid is never shot."""
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"claude" in cmdline:
                os.killpg(pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        """Liveness by /proc state — a zombie still HAS a /proc entry,
        so existence alone would keep a finished (unreaped) seat
        \"running\" forever. State Z is dead."""
        if not pid:
            return False
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            state = stat.rsplit(")", 1)[1].split()[0]
            return state != "Z"
        except (OSError, IndexError):
            return False

    # -- launch -------------------------------------------------------------

    def launch(self, role: str, action: dict, *,
               resume: dict | None = None) -> dict:
        """Start one engagement and return its acknowledgment.

        Fresh seats get the full prompt/plan treatment; ``resume``
        (from ``continue_engagement``) re-enters an existing workspace
        with ``claude --resume <session-id>`` — no new fork, no GC, no
        memory ship: the workspace already carries all of it."""
        if role not in ROLE_NAMES:
            return {"ok": False, "error": f"unknown collaborator role: {role}"}

        side_dir: Path | None = None
        if resume is None:
            evidence_index = self.ledger.neutral_experiment_index()
            selected_experiments: list[dict] = []
            if not (role == "proposer" and action.get("scope") == "open"):
                for experiment_id in action.get("experiment_ids") or []:
                    result = self.ledger.inspect_experiment(
                        {"experiment_id": experiment_id}
                    )
                    if result.get("ok"):
                        selected_experiments.append(result)
            try:
                prompt = build_collaboration_prompt(
                    role,
                    action,
                    goal=self.config.goal or "(no goal stated)",
                    gate_block=self.config.gate_block
                    or "(no constraints stated)",
                    current_judgment=self.ledger.current_judgment(),
                    evidence_index=evidence_index,
                    selected_experiments=selected_experiments,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}

            collaborator_id = self._next_call_id(role)
            self._gc_forks()

            # -- seat plan: workspace, time box ----------------------------
            # Seat ≠ World (docs/design/席位工作区重设计). A seat's home
            # is its directory in the scratch area; the live world is
            # readable reality; a world to MODIFY is created by behavior —
            # pre-built for a speculative executor (its brief already is
            # one), self-served through the make-experiment kit by anyone
            # else. Cognitive seats get no fork by default: their value is
            # independent cognition, not an independent filesystem.
            seat_home = self._dir_of(collaborator_id)
            work_dir = self.world.work
            workspace = "read-only"
            workspace_note = ""

            if role == "executor":
                workspace = str(action.get("workspace") or "current")
                if workspace not in {"current", "isolated"}:
                    return {
                        "ok": False,
                        "error": "executor.workspace must be current|isolated",
                    }
                timeout = _box_from_action(
                    action, self.config.work_default_minutes * 60,
                    self.config.seat_timeout_max_minutes)
                if workspace == "isolated":
                    # mainline ownership (current) vs alternative world
                    # (isolated): the speculative executor's brief IS a
                    # world-changing test, so its world is pre-built —
                    # cheap fork (data dirs symlinked into the read-only
                    # originals; nothing legitimate writes there). The
                    # PI's records never ship: the collaborator gets the
                    # world, not the ledger.
                    source = self.config.node_world or self.world.work
                    side_dir = seat_home / "world"
                    _fork_world(Path(source), side_dir)
                    work_dir = side_dir
            else:
                # searcher / proposer / challenger / reviewer: own scratch,
                # live world readable, experiment kit self-served.
                if role == "searcher":
                    timeout = _box_from_action(
                        action, self.config.consult_timeout_seconds,
                        self.config.seat_timeout_max_minutes)
                else:
                    timeout = _box_from_action(
                        action, self.config.cognitive_timeout_seconds,
                        self.config.seat_timeout_max_minutes)
                work_dir = seat_home / "scratch"
                work_dir.mkdir(parents=True, exist_ok=True)
                workspace = "scratch"
                self._write_experiment_kit(work_dir)
                workspace_note = (
                    f"Workspace: this scratch directory is yours. The live "
                    f"research world is readable at {self.world.work} — "
                    f"inspect it freely (code, git history, benchmarks, "
                    f"data) and treat it as read-only reality: what you "
                    f"change belongs in your scratch. When a question can "
                    f"only be answered by modifying the world, run "
                    f"``./make-experiment`` here and work inside the "
                    f"disposable copy it creates; report what it showed."
                )
                if role == "reviewer":
                    # The one seat whose job is the run's history — it
                    # reads the record directly, no packing, no fork.
                    workspace_note += (
                        f"\nThe run's record is readable at "
                        f"{self.world.state_dir}/ — the wire, prior "
                        f"judgments, collaborator reports, research "
                        f"memory. It is your material; dig freely."
                    )
                if self.config.node_world is not None:
                    workspace_note += (
                        f"\nThe pristine baseline world (untouched by this "
                        f"run) is readable at {self.config.node_world}."
                    )

            if workspace_note:
                prompt += f"\n\n{workspace_note}"
            continued_from = ""
            session_hint: str | None = None
        else:
            # -- continuation: the seat's world is the record --------------
            try:
                prompt = build_continuation_prompt(action)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            collaborator_id = self._next_call_id("executor")
            work_dir = Path(resume["work_dir"])
            side_dir = (Path(resume["side_dir"])
                        if resume.get("side_dir") else None)
            workspace = str(resume.get("mode") or "current")
            timeout = _box_from_action(
                action, self.config.work_default_minutes * 60,
                self.config.seat_timeout_max_minutes)
            continued_from = str(resume.get("continued_from") or "")
            session_hint = str(resume.get("session_id") or "") or None

        # P4, pointer not feed: every seat learns the research memory
        # EXISTS and where; whether and what to read stays each seat's
        # own professional call (a narrow executor may skip it; a
        # challenger that never checks old judgments has not done its
        # job — that policing is the role's, not the harness's). The
        # live world's copy is pointed at — seats no longer carry one.
        if self.ledger.research_memory_path.is_file():
            prompt += (
                "\n\nResearch memory: this run's research memory is at "
                f"{self.ledger.research_memory_path} — the important "
                "recognitions, directions, and evidence references the "
                "Scientist has recorded this run. Consultable as your "
                "own judgment requires."
            )

        # -- start the seat; the directory is born --------------------------
        started = time.time()
        raw_path = self._raw_dir(collaborator_id) / "raw.txt"
        handle = raw_path.open("wb")
        # run-by-run isolation at the single spawn chokepoint: the seat
        # sees the run world's .claude and home and nothing of the
        # user's — no ambient session identity (CLAUDE_*), no inherited
        # credentials/endpoints (ANTHROPIC_*), no user HOME. The spec's
        # env, CLAUDE_CONFIG_DIR and HOME are the only claude-facing
        # state that reaches the child.
        env = {k: v for k, v in os.environ.items()
               if not (k.startswith("CLAUDE") or k.startswith("ANTHROPIC"))}
        if self.config.env:
            env.update(
                {str(k): str(v) for k, v in self.config.env.items()})
        config_dir, home_dir = self._world_runtime()
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        env["HOME"] = str(home_dir)
        argv = self._command_payload(_SEAT_TOOLS,
                                     resume_session=session_hint)
        print(f"[research-team] {role} {collaborator_id} running "
              f"(box {timeout}s, workspace {workspace}"
              + (", resumed" if session_hint else "") + ")",
              flush=True)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(work_dir), stdin=subprocess.PIPE, stdout=handle,
                stderr=subprocess.STDOUT, env=env, text=True,
                # own process group: a time-box kill must take the whole
                # tree (claude's bash/bench children). proc.pid is
                # recorded so startup recovery can kill the one survivor
                # case nothing else closes (SIGKILL of the scientist).
                start_new_session=True,
            )
        except OSError as exc:
            # a seat that cannot even start is a receipt, not a crash of
            # the whole episode — the PI sees the failure and re-plans.
            # No manifest was written: the directory never existed as an
            # engagement, and sweep passes it by.
            try:
                handle.close()
            except OSError:
                pass
            return {"ok": False, "call_id": collaborator_id,
                    "collaborator_id": collaborator_id, "role": role,
                    "status": "failed",
                    "error": f"failed to start seat process: {exc}"}
        pid_file = raw_path.parent / "proc.pid"
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        (raw_path.parent / "prompt.txt").write_text(prompt, encoding="utf-8")
        (raw_path.parent / "manifest.json").write_text(
            json.dumps({
                "role": role,
                "collaborator_id": collaborator_id,
                "episode_id": self.episode_id,
                "box": timeout,
                "started": started,
                "work_dir": str(work_dir),
                "side_dir": str(side_dir) if side_dir else None,
                "mode": workspace,
                "argv": argv,
                "session_hint": session_hint,
                "continued_from": continued_from or None,
                "brief": str(action.get("brief") or "").strip(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8")
        proc.stdin.write(prompt)
        proc.stdin.close()
        self._procs[collaborator_id] = proc
        try:
            handle.close()
        except OSError:
            pass
        evidence = self._evidence_envelope(collaborator_id, side_dir,
                                           started)
        return {"ok": True, "call_id": collaborator_id,
                "collaborator_id": collaborator_id, "role": role,
                "status": "running", "mode": workspace,
                "box_seconds": timeout,
                "harness_evidence": evidence,
                **({"continued_from": continued_from}
                   if continued_from else {})}

    def _evidence_envelope(self, call_id: str,
                           side_dir: Path | None,
                           started: float) -> dict:
        return {
            "transcript": (
                f"{self.world.work}/.scientist/assistant/{call_id}/"
                "raw.txt"),
            "digest": (
                f"{self.world.work}/.scientist/assistant/{call_id}/"
                "digest.json"),
            "workspace": (
                f"{self.world.scratch}/fresh-{call_id}"
                if side_dir is not None else str(self.world.work)),
            "ran_for_seconds": round(time.time() - started),
        }

    # -- collection ---------------------------------------------------------

    def _manifest(self, call_id: str) -> dict:
        try:
            return json.loads(
                (self._dir_of(call_id) / "manifest.json").read_text(
                    encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def collect(self, call_id: str, *, status_hint: str = "",
                note: str = "") -> dict:
        """The ONE finalizer: turn a finished (or killed, or crashed)
        engagement's directory into ``digest.json`` + the report dict.
        ``status_hint`` names the situation explicitly (timeout-salvaged
        / crash-salvaged); without it the transcript is parsed normally.
        Idempotence is the file's: an existing digest.json short-circuits.
        """
        d = self._dir_of(call_id)
        if (d / "digest.json").exists():
            try:
                return json.loads(
                    (d / "digest.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        mani = self._manifest(call_id)
        role = str(mani.get("role") or "seat")
        mode = str(mani.get("mode") or "unknown")
        side_dir = (Path(mani["side_dir"])
                    if mani.get("side_dir") else None)
        started = float(mani.get("started") or time.time())
        continued_from = str(mani.get("continued_from") or "")
        prompt = ""
        try:
            prompt = (d / "prompt.txt").read_text(encoding="utf-8")
        except OSError:
            pass
        try:
            raw = (d / "raw.txt").read_text(encoding="utf-8",
                                             errors="replace")
        except OSError:
            raw = ""
        session_id = _session_id_from_raw(raw)
        (d / "proc.pid").unlink(missing_ok=True)
        self._procs.pop(call_id, None)
        evidence = self._evidence_envelope(call_id, side_dir, started)
        common = {
            "call_id": call_id,
            "collaborator_id": call_id,
            "role": role,
            "mode": mode,
            "started": started,
            "finished_at": time.time(),
            "session_id": session_id,
            **({"continued_from": continued_from}
               if continued_from else {}),
            "harness_evidence": evidence,
        }
        error = note
        if status_hint:
            # Salvage: a killed or crashed engagement still leaves a
            # partial transcript, and the last substantive text + tool
            # count reach the PI as a marked report — a time-box expiry
            # must never silently discard the engagement's whole thinking
            # (the 2026-08-28 proposers died with a diagnosis in hand
            # that nobody received).
            partial, tool_calls = _partial_report(raw)
            if partial or tool_calls:
                cap = self.config.distill_word_cap
                report, _truncated = _cap_words(partial, cap)
                digest = {
                    **common,
                    "status": status_hint,
                    "tool_calls": tool_calls,
                    "self_report_digest": report,
                    "report_digest": report,
                    "note": note,
                    "ok": True,
                    "channel": "belief",
                }
                self._persist_raw(call_id, prompt, digest)
                self._note(call_id, role,
                           f"{mani.get('brief') or ''} -> [{status_hint}] "
                           f"{report}", None)
                return digest
            digest = {**common, "status": "failed", "ok": False,
                      "error": note or f"{status_hint} with no salvageable "
                                       "output"}
            self._persist_raw(call_id, prompt, digest)
            self._note(call_id, role,
                       f"{mani.get('brief') or ''} -> {note}", None)
            return digest
        cap = self.config.distill_word_cap
        text, usage = _decode_stream(raw)
        tail = _parse_tail(text) or {}
        diff_summary, d_trunc = _cap_words(
            str(tail.get("diff_summary") or ""), cap)
        self_report, s_trunc = _cap_words(str(
            tail.get("report_digest")
            or tail.get("self_report_digest")
            or tail.get("answer_digest")
            or text
            or ""
        ), cap)
        metrics = tail.get("metrics") if isinstance(
            tail.get("metrics"), dict) else {}
        digest = {
            **common,
            "ok": True,
            "status": "done" if tail else "unparsed",
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "report_digest": self_report,
            "evidence": tail.get("evidence") or [],
            "artifacts": tail.get("artifacts") or [],
            "uncertainty": tail.get("uncertainty") or "",
            "recommended_follow_up": tail.get("recommended_follow_up") or "",
            "metrics": metrics,
            "truncated": bool(d_trunc or s_trunc),
            **({"error": error} if error else {}),
            # The world itself is the truth channel; these numbers are the
            # assistant's own report.
            "channel": "belief",
        }
        self._persist_raw(call_id, prompt, digest)
        self._note(call_id, role,
                   f"{mani.get('brief') or ''} -> {self_report}", usage)
        return digest

    def _unfinalized(self) -> list[Path]:
        out = []
        for base in self._seat_roots():
            if not base.is_dir():
                continue
            out += [
                entry for entry in base.iterdir()
                if entry.is_dir()
                and (entry / "manifest.json").is_file()
                and not (entry / "digest.json").exists()
            ]
        return sorted(out)

    def sweep(self, now: float | None = None) -> list[dict]:
        """Collect every engagement that has finished or blown its box.
        The one enforcement point of the time box; called at the loop's
        turn top, inside ``wait``, and (with kills) at exit and startup.
        """
        now = time.time() if now is None else now
        reports: list[dict] = []
        for entry in self._unfinalized():
            call_id = entry.name
            mani = self._manifest(call_id)
            started = float(mani.get("started") or now)
            box = float(mani.get("box") or 0)
            try:
                pid = int((entry / "proc.pid").read_text().strip())
            except (OSError, ValueError):
                pid = 0
            proc = self._procs.get(call_id)
            rc = None
            if proc is not None:
                rc = proc.poll()          # reaps a finished seat
                alive = rc is None
            else:
                alive = self._pid_alive(pid)
            if alive and now - started <= box:
                continue
            if alive:
                if proc is not None:
                    self._kill_tree(proc)
                else:
                    self._kill_pid(pid)
                reports.append(self.collect(
                    call_id, status_hint="timeout-salvaged",
                    note=(f"{mani.get('role')} {call_id} exceeded its "
                          f"time box ({int(box)}s) and was stopped")))
            else:
                error = f"claude exited {rc}" if rc else ""
                reports.append(self.collect(call_id, note=error))
        return reports

    def pending(self, now: float | None = None) -> list[dict]:
        """Status rows for engagements still inside their boxes."""
        now = time.time() if now is None else now
        rows = []
        for entry in self._unfinalized():
            mani = self._manifest(entry.name)
            started = float(mani.get("started") or now)
            rows.append({
                "collaborator_id": entry.name,
                "role": mani.get("role"),
                "box_seconds": mani.get("box"),
                "elapsed_seconds": round(now - started),
            })
        return rows

    def _has_unread_reports(self) -> bool:
        """Non-consuming peek: is there a finished report the PI has not
        been handed yet? The arrival signal ``wait(mode="any")`` sleeps
        on — ``_take_reports`` would consume it."""
        base = self._base()
        if not base.is_dir():
            return False
        for entry in base.iterdir():
            if ((entry / "digest.json").is_file()
                    and not (entry / "read.marker").exists()):
                return True
        return False

    def _take_reports(self, only: str | None = None) -> list[dict]:
        """Reports not yet delivered to the PI; delivery touches
        ``read.marker`` — exactly-once by a file op, resume-safe. ``only``
        takes a single engagement's report without consuming anyone
        else's (a synchronous engage must not eat a sibling's pending
        delivery)."""
        out = []
        base = self._base()
        if not base.is_dir():
            return out
        for entry in sorted(base.iterdir()):
            if only is not None and entry.name != only:
                continue
            digest = entry / "digest.json"
            marker = entry / "read.marker"
            if not digest.is_file() or marker.exists():
                continue
            try:
                report = json.loads(digest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            marker.touch()
            out.append(report)
        return out

    def poll_completions(self) -> list[dict]:
        """Sweep, then hand the PI every not-yet-delivered report."""
        self.sweep()
        return self._take_reports()

    # -- the PI-facing verbs ------------------------------------------------

    def engage(self, role: str, action: dict) -> dict:
        """Run one engagement to completion; return its report.

        Blocks for the whole engagement (the Reviewer's listening
        semantics; also the synchronous back-compat surface)."""
        ack = self.launch(role, action)
        if not ack.get("ok"):
            return ack
        call_id = ack["collaborator_id"]
        while not (self._dir_of(call_id) / "digest.json").exists():
            self.sweep()
            if (self._dir_of(call_id) / "digest.json").exists():
                break
            time.sleep(0.25)
        reports = self._take_reports(only=call_id)
        # the report was written; take it even if a concurrent reader
        # (there is none today — single-threaded dispatch) got the marker
        return reports[0] if reports else self.collect(call_id)

    def engage_async(self, role: str, action: dict) -> dict:
        """Fire one engagement; return its acknowledgment. The report
        arrives later — as a turn-top observation, or through ``wait``."""
        return self.launch(role, action)

    def wait_for_seats(self, timeout_minutes: float | None = None,
                       mode: str = "all") -> dict:
        """Block until pending engagements finish (each within its own
        box) or the bound elapses; their reports are this call's result.

        ``mode="any"`` returns on the FIRST arrival instead of the last.
        With a long mainline engagement in flight, waiting for everyone
        holds a finished speculative report hostage until the slowest box
        expires — a PI that cannot harvest early learns not to keep
        speculation pending, and parallelism collapses into serial
        babysitting (the r3 reading: dispatches into an empty pool). The
        rest keep running and surface through later waits and turn tops.
        """
        any_mode = str(mode) == "any"
        t0 = time.monotonic()
        deadline = (None if timeout_minutes is None
                    else t0 + max(1, int(timeout_minutes)) * 60)
        while True:
            self.sweep()
            if not self._unfinalized():
                break
            if any_mode and self._has_unread_reports():
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        finished = self._take_reports()
        if not finished and not self._unfinalized():
            return {"ok": True, "finished": [], "still_running": [],
                    "note": "no engagement pending",
                    "waited_seconds": round(time.monotonic() - t0, 1)}
        return {"ok": True, "finished": finished,
                "still_running": self.pending(),
                "mode": "any" if any_mode else "all",
                "waited_seconds": round(time.monotonic() - t0, 1)}

    def continue_engagement(self, action: dict) -> dict:
        """Resume a FINISHED Executor engagement's claude session in its
        existing workspace, with the PI's brief as the new instruction.
        Executor only — the other roles' value is a fresh reading."""
        call_id = str(action.get("collaborator_id") or "").strip()
        if not call_id or not str(action.get("brief") or "").strip():
            return {"ok": False,
                    "error": "continue_engagement requires "
                             "collaborator_id and brief"}
        digest_path = self._dir_of(call_id) / "digest.json"
        if not digest_path.is_file():
            return {"ok": False, "error": (
                f"no finished engagement record under '{call_id}' — "
                "open a fresh engagement instead")}
        try:
            digest = json.loads(digest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False,
                    "error": f"unreadable engagement record: {exc}"}
        if str(digest.get("role") or "") != "executor":
            return {"ok": False, "error": (
                f"only Executor engagements continue; "
                f"'{call_id}' is {digest.get('role')} — those roles open "
                "fresh each time")}
        if str(digest.get("status") or "") == "failed":
            return {"ok": False, "error": (
                f"'{call_id}' failed without salvageable output; a resume "
                "would replay the same failure — open a fresh engagement")}
        session_id = str(digest.get("session_id") or "")
        if not session_id:
            return {"ok": False, "error": (
                f"'{call_id}' carries no session id to resume (transcript "
                "shape changed or was empty) — open a fresh engagement")}
        for entry in self._unfinalized():
            mani = self._manifest(entry.name)
            if mani.get("session_hint") == session_id:
                return {"ok": False, "error": (
                    f"'{call_id}' is still running as {entry.name}; its "
                    "report arrives when it finishes")}
        mani = self._manifest(call_id)
        work_dir = mani.get("work_dir") or digest.get(
            "harness_evidence", {}).get("workspace")
        if not work_dir or not Path(work_dir).is_dir():
            return {"ok": False, "error": (
                f"the workspace of '{call_id}' was reclaimed (fork "
                "retention) — open a fresh executor engagement")}
        return self.launch("executor", action, resume={
            "session_id": session_id,
            "work_dir": work_dir,
            "side_dir": mani.get("side_dir"),
            "continued_from": call_id,
            "mode": mani.get("mode") or digest.get("mode") or "current",
        })

    def cancel_engagement(self, action: dict) -> dict:
        """Stop-loss: stop ONE running engagement before its box expires
        and salvage its partial transcript. The verb for a speculative
        candidate the world has already passed by — burning its remaining
        box is pure cost. The salvaged report is this call's own result
        (consumed here, so a turn top will not re-deliver it); a
        cancelled Executor whose session id survived can still be
        continued later."""
        call_id = str(action.get("collaborator_id") or "").strip()
        reason = str(action.get("reason") or "").strip()
        if not call_id:
            return {"ok": False,
                    "error": "cancel_engagement requires collaborator_id"}
        d = self._dir_of(call_id)
        if not (d / "manifest.json").is_file():
            return {"ok": False, "error": (
                f"no engagement record under '{call_id}'")}
        if (d / "digest.json").exists():
            return {"ok": False, "error": (
                f"'{call_id}' has already finished — its report is "
                "delivered like any other")}
        proc = self._procs.get(call_id)
        if proc is not None:
            self._kill_tree(proc)
        else:
            try:
                pid = int((d / "proc.pid").read_text().strip())
            except (OSError, ValueError):
                pid = 0
            self._kill_pid(pid)
        note = reason or "cancelled by the Scientist before its box expired"
        self.collect(call_id, status_hint="cancelled", note=note)
        reports = self._take_reports(only=call_id)
        return reports[0] if reports else {
            "ok": True, "cancelled": call_id, "status": "cancelled",
            "note": note}

    def shutdown_pending(self, reason: str = (
            "the episode ended before this engagement finished")
                         ) -> list[dict]:
        """Episode exit: kill what is still running, salvage everything.
        The reason must not read like a timeout — these are crash-salved
        by situation, the same label startup recovery writes."""
        reports: list[dict] = []
        for entry in self._unfinalized():
            call_id = entry.name
            proc = self._procs.get(call_id)
            if proc is not None:
                self._kill_tree(proc)
            else:
                try:
                    pid = int((entry / "proc.pid").read_text().strip())
                except (OSError, ValueError):
                    pid = 0
                self._kill_pid(pid)
            reports.append(self.collect(
                call_id, status_hint="crash-salvaged", note=reason))
        return reports

    def _reconcile(self) -> None:
        """Startup recovery: an engagement directory without digest.json
        belongs to an episode that died before collecting (a SIGKILL of
        the scientist — no ``finally`` ran). Kill the surviving seat,
        crash-salvage the transcript, and resume the call-id counter past
        the highest sequence ever used (a reused id would truncate the
        orphan's still-growing raw.txt)."""
        for base in self._seat_roots():
            if not base.is_dir():
                continue
            for entry in base.iterdir():
                match = self._CALL_SEQ_RE.search(entry.name) \
                    if entry.is_dir() else None
                if match:
                    self._counter = max(self._counter, int(match.group(1)))
        for entry in self._unfinalized():
            try:
                pid = int((entry / "proc.pid").read_text().strip())
            except (OSError, ValueError):
                pid = 0
            self._kill_pid(pid)
            self.collect(
                entry.name, status_hint="crash-salvaged",
                note="recovered by the harness on resume: the episode "
                     "died before this engagement was finalized")
