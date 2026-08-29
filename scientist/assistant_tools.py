"""One subprocess runtime for research-team engagements.

SYNCHRONOUS by design: a collaborator call runs its claude session to
completion and RETURNS the report as the tool result — give command, run
claude, response, report. There is no background job queue, no mail pump,
no wait tool: the time box is enforced at exactly one place (the blocking
wait), a crashed episode kills its seat in ``finally`` (nothing to
reconcile), and the tool_call/tool_result pairing the model natively
understands is the only delivery channel. (The async predecessor spent
its whole complexity budget on pathologies this design cannot have.)

Seat discipline is enforced by the workspace, not by tool denial: every
seat gets the full tool face; cognitive seats (proposer, challenger,
searcher-with-lab) run inside a disposable fork of the current world
whose data directories are symlinks into the read-only originals —
prototyping is free, nothing reaches the live tree, and the digest is
the only channel home. Raw trajectories use the legacy
``.scientist/assistant`` archive path and never enter the Scientist's
active context.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .collaboration import ROLE_NAMES, build_collaboration_prompt

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Blinded cognitive seats (no Bash/Write) burned whole engagements fighting
# the permission wall instead of proposing: the briefs demand evidence
# ("establish with evidence, prototype"), and npz data cannot be read with
# Read/Grep. Every seat therefore gets the SAME full tool face — what
# separates a cognitive seat from an executor is the workspace (disposable
# fork vs the live tree) and the digest contract, nothing else.
_SEAT_TOOLS = "Read,Grep,Glob,Edit,Write,Bash,WebSearch,WebFetch,Task"

_FORK_NOTE = (
    "Workspace: a DISPOSABLE COPY of the world, made just for you — data "
    "directories are symlinks into the read-only originals, src/ is your "
    "own private copy. You have full tool access: read anything, run code, "
    "prototype freely inside this copy. Nothing you change here reaches "
    "the live world; your ONLY deliverable is the report you return.")


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


# A forked data directory below this size is copied instead of symlinked;
# anything at or above it (or named ``benchmarks``, the harness layout) is
# symlinked into the read-only original so a 9 GB package costs nothing.
_FORK_SYMLINK_MIN_BYTES = 512 * 1024 * 1024


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _fork_world(source: Path, dest: Path) -> None:
    """Disposable copy of a world for a cognitive seat.

    Small trees (src/, scripts/, .git, docs) are copied so prototyping is
    free; data-scale directories — anything named ``benchmarks`` or 512 MB
    and up — become symlinks into the source, where the read-only mount
    rejects writes at the kernel level. The PI's records (``.scientist``)
    never ship: a forked collaborator gets the world, not the ledger.
    """
    dest.mkdir(parents=True, exist_ok=False)
    source = Path(source).absolute()   # symlink targets must be absolute
    for entry in sorted(source.iterdir()):
        if entry.name == ".scientist":
            continue
        target = dest / entry.name
        if entry.is_dir() and (
                entry.name == "benchmarks"
                or _tree_bytes(entry) >= _FORK_SYMLINK_MIN_BYTES):
            target.symlink_to(entry, target_is_directory=True)
        elif entry.is_dir():
            shutil.copytree(entry, target,
                            ignore=shutil.ignore_patterns(".scientist"))
        else:
            shutil.copy2(entry, target)


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
    consult_timeout_seconds: int = 1800
    cognitive_timeout_seconds: int = 5400
    work_default_minutes: int = 60
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
                    "consult_timeout_seconds", 1800)),
            cognitive_timeout_seconds=int(
                (spec.get("budget") or {}).get(
                    "cognitive_timeout_seconds", 5400)),
            work_default_minutes=int(
                (spec.get("budget") or {}).get(
                    "work_default_minutes", 60)),
            goal=str(spec.get("goal") or ""),
            gate_block=str(spec.get("gate_block") or ""),
        )


class InWorldAssistant:
    """Synchronous Claude subprocess runtime behind the four research
    roles: ``engage`` runs one seat to completion and returns its report."""

    _CALL_SEQ_RE = re.compile(r"-(\d{3})$")

    # forks are kept for continuation but not forever: a 7-day run with
    # dozens of engagements must not accumulate workspaces without bound
    _FORK_KEEP_MAX = 24
    # never GC a fork younger than this — a same-turn batch of seats may
    # be running in freshly created forks (boxes are capped at 180 min)
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
        self._reconcile()

    def _reconcile(self) -> None:
        """Startup crash recovery — insurance for the one window sync
        cannot close: a SIGKILL of the scientist itself (no ``finally``
        runs). The killed episode's seat survives in its own process
        group with a recorded pid: kill it, harvest its transcript into a
        crash-salvage digest (evidence must survive us), and resume the
        call-id counter past the highest sequence ever used (a reused id
        would truncate the orphan's still-growing raw.txt)."""
        base = self.world.work / ".scientist" / "assistant"
        if not base.is_dir():
            return
        for entry in base.iterdir():
            match = self._CALL_SEQ_RE.search(entry.name) if entry.is_dir() \
                else None
            if match:
                self._counter = max(self._counter, int(match.group(1)))
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or (entry / "digest.json").exists():
                continue
            try:
                raw = (entry / "raw.txt").read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
            pid_file = entry / "proc.pid"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                    if b"claude" in cmdline:
                        os.killpg(pid, signal.SIGKILL)
                except (OSError, ValueError):
                    pass
            partial, tools = _partial_report(raw)
            (entry / "digest.json").write_text(
                json.dumps({
                    "collaborator_id": entry.name,
                    "status": "crash-salvaged",
                    "tool_calls": tools,
                    "self_report_digest": partial[:4000],
                    "note": "recovered by the harness on resume: the "
                            "episode died before this engagement was "
                            "finalized",
                }, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self._note(entry.name, "seat", partial, None)

    # -- plumbing ----------------------------------------------------------

    def _next_call_id(self, kind: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{kind}-{self.episode_id}-{self._counter:03d}"

    def _raw_dir(self, call_id: str) -> Path:
        d = self.world.work / ".scientist" / "assistant" / call_id
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

    def _command_payload(self, allowed_tools: str) -> list[str]:
        payload = [
            self.config.command, "-p",
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

    def _gc_forks(self) -> None:
        """Trim kept forks to the most recent _FORK_KEEP_MAX. Only forks
        older than _FORK_MIN_AGE_SECONDS are eligible, so a sibling seat
        running in a batch (box capped at 180 min) is never touched."""
        root = self.world.scratch
        now = time.time()
        try:
            forks = sorted(
                (p for p in root.glob("fresh-*") if p.is_dir()),
                key=lambda p: p.stat().st_mtime)
        except OSError:
            return
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

    # -- the one public operation -------------------------------------------

    def engage(self, role: str, action: dict) -> dict:
        """Run one fresh role engagement to completion; return its report.

        This call blocks for the whole engagement (minutes to hours, per
        its time box) — the report IS the tool result."""
        if role not in ROLE_NAMES:
            return {"ok": False, "error": f"unknown collaborator role: {role}"}

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
                gate_block=self.config.gate_block or "(no constraints stated)",
                current_judgment=self.ledger.current_judgment(),
                evidence_index=evidence_index,
                selected_experiments=selected_experiments,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        collaborator_id = self._next_call_id(role)
        self._gc_forks()

        # -- seat plan: workspace, time box, prompt contract --------------
        work_dir = self.world.work
        side_dir: Path | None = None
        workspace = "read-only"
        workspace_note = ""

        if role == "searcher":
            read = action.get("read", "none")
            if read not in {"none", "node", "lab"}:
                return {"ok": False, "error": "searcher.read must be none|node|lab"}
            timeout = _box_from_action(
                action, self.config.consult_timeout_seconds,
                self.config.seat_timeout_max_minutes)
            if read == "none":
                work_dir = self.world.scratch
                workspace_note = (
                    "Workspace: bare scratch. This engagement is "
                    "literature-only: work from the open literature and "
                    "your own knowledge, not from the world's data.")
            elif read == "node":
                if self.config.node_world is None:
                    return {"ok": False, "error": "searcher read=node requires a node world"}
                work_dir = Path(self.config.node_world)
                workspace_note = (
                    "Workspace: the pristine node world, mounted "
                    "read-only — you may read it and run code against it, "
                    "but it rejects writes; report only.")
            else:
                # lab: the live tree FORKED — full tools must never be
                # able to touch the live src/ directly.
                side_dir = self.world.scratch / f"fresh-{collaborator_id}"
                _fork_world(work_dir, side_dir)
                work_dir = side_dir
            workspace = str(read)
        elif role == "executor":
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
                source = self.config.node_world or self.world.work
                side_dir = self.world.scratch / f"fresh-{collaborator_id}"
                # cheap fork (data dirs symlinked into the read-only
                # originals — nothing legitimate writes there; /scratch is
                # the sanctioned space for generated data). The PI's
                # records never ship: an isolated collaborator gets the
                # world, not the ledger.
                _fork_world(Path(source), side_dir)
                work_dir = side_dir
        else:
            # proposer / challenger: full tools inside a disposable fork
            # of the CURRENT world (not the pristine template — a
            # proposal must see the incumbent solver). The briefs demand
            # evidence; the fork guarantees the live world stays
            # untouched; the digest is the only channel home. Longer box:
            # evidence-backed proposing is the expensive part of a run.
            workspace = "fork"
            timeout = _box_from_action(
                action, self.config.cognitive_timeout_seconds,
                self.config.seat_timeout_max_minutes)
            side_dir = self.world.scratch / f"fresh-{collaborator_id}"
            _fork_world(work_dir, side_dir)
            work_dir = side_dir
            workspace_note = _FORK_NOTE

        if workspace_note:
            prompt += f"\n\n{workspace_note}"

        # -- run the seat synchronously to its time box --------------------
        started = time.time()
        raw_path = self._raw_dir(collaborator_id) / "raw.txt"
        handle = raw_path.open("wb")
        env = dict(os.environ)
        if self.config.env:
            env.update(
                {str(k): str(v) for k, v in self.config.env.items()})
        print(f"[research-team] {role} {collaborator_id} running "
              f"(box {timeout}s, workspace {workspace})", flush=True)
        error = ""
        try:
            proc = subprocess.Popen(
                self._command_payload(_SEAT_TOOLS),
                cwd=str(work_dir), stdin=subprocess.PIPE, stdout=handle,
                stderr=subprocess.STDOUT, env=env, text=True,
                # own process group: a time-box kill must take the whole
                # tree (claude's bash/bench children). proc.pid is
                # recorded so _reconcile can kill the one survivor case
                # sync cannot close (SIGKILL of the scientist itself).
                start_new_session=True,
            )
            pid_file = raw_path.parent / "proc.pid"
            pid_file.write_text(str(proc.pid), encoding="utf-8")
            proc.stdin.write(prompt)
            proc.stdin.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
                error = (f"{role} {collaborator_id} exceeded its time box "
                         f"({timeout}s) and was stopped")
            else:
                pid_file.unlink(missing_ok=True)
                if proc.returncode != 0:
                    error = f"claude exited {proc.returncode}"
        except OSError as exc:
            # a seat that cannot even start is a receipt, not a crash of
            # the whole episode — the PI sees the failure and re-plans
            try:
                handle.close()
            except OSError:
                pass
            return {"ok": False, "call_id": collaborator_id,
                    "collaborator_id": collaborator_id, "role": role,
                    "status": "failed",
                    "error": f"failed to start seat process: {exc}"}
        try:
            handle.close()
        except OSError:
            pass
        return self._finalize(
            call_id=collaborator_id, role=role, instruction=str(
                action.get("brief") or "").strip(),
            mode=workspace, side_dir=side_dir, started=started,
            raw_path=raw_path, prompt=prompt, error=error)

    def _finalize(self, *, call_id: str, role: str, instruction: str,
                  mode: str, side_dir: Path | None, started: float,
                  raw_path: Path, prompt: str,
                  error: str = "") -> dict:
        try:
            raw = raw_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        # Side dirs are always kept — also on success. A fork is cheap
        # (small copies + symlinks; bulk data belongs in /scratch), and
        # big projects continue across a SEQUENCE of engagements: a
        # successor needs the predecessor's workspace, and this evidence
        # block is the pointer the PI forwards.
        evidence = {
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
        if error:
            # Salvage first: a killed or crashed engagement still leaves
            # a partial transcript, and the last substantive text +
            # tool count reach the PI as a marked report — a time-box
            # expiry must never silently discard the engagement's whole
            # thinking (the 2026-08-28 proposers died with a diagnosis in
            # hand that nobody received).
            partial, tool_calls = _partial_report(raw)
            if partial or tool_calls:
                status = ("timeout-salvaged" if "time box" in error
                          else "crash-salvaged")
                cap = self.config.distill_word_cap
                report, _truncated = _cap_words(partial, cap)
                self._persist_raw(call_id, prompt, {
                    "mode": mode,
                    "role": role,
                    "collaborator_id": call_id,
                    "status": status,
                    "tool_calls": tool_calls,
                    "self_report_digest": report,
                    "note": error,
                })
                self._note(call_id, role,
                           f"{instruction} -> [{status}] {report}",
                           None)
                return {"ok": True, "call_id": call_id,
                        "collaborator_id": call_id, "role": role,
                        "status": status,
                        "mode": mode,
                        "self_report_digest": report,
                        "report_digest": report,
                        "tool_calls": tool_calls,
                        "note": error,
                        "harness_evidence": evidence,
                        "channel": "belief"}
            self._persist_raw(call_id, prompt, {"error": error})
            self._note(call_id, role, f"{instruction} -> {error}", None)
            return {"ok": False, "call_id": call_id,
                    "collaborator_id": call_id, "role": role,
                    "status": "failed", "error": error,
                    "harness_evidence": evidence}
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
        self._persist_raw(call_id, prompt, {
            "mode": mode,
            "role": role,
            "collaborator_id": call_id,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "metrics": metrics,
        })
        self._note(call_id, role,
                   f"{instruction} -> {self_report}", usage)
        return {
            "ok": True,
            "call_id": call_id,
            "collaborator_id": call_id,
            "role": role,
            "status": "done" if tail else "unparsed",
            "mode": mode,
            "diff_summary": diff_summary,
            "self_report_digest": self_report,
            "report_digest": self_report,
            "evidence": tail.get("evidence") or [],
            "artifacts": tail.get("artifacts") or [],
            "uncertainty": tail.get("uncertainty") or "",
            "recommended_follow_up": tail.get("recommended_follow_up") or "",
            "metrics": metrics,
            "truncated": bool(d_trunc or s_trunc),
            "harness_evidence": evidence,
            # The world itself is the truth channel; these numbers are the
            # assistant's own report.
            "channel": "belief",
        }
