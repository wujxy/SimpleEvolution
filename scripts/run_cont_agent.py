"""One Claude Code session, one world, one long shift.

The continuous coding-agent arm (§12 of the seat design doc): a single
``claude -p`` invocation owns ONE persistent worktree for the whole wall
budget.  The harness never interrupts the session; it only watches:

* every ``--snapshot-every`` seconds it freezes src/ into a side commit via a
  temporary GIT_INDEX_FILE (the agent never notices; PROGRESS.md and build
  artifacts stay untracked because the pathspec is ``src`` only);
* optionally runs an indicative verify+bench of that snapshot on a SEPARATE
  pinned core (``--live-every N`` distinct snapshots; indicative only — it
  contends with the agent's own benching);
* after the shift ends, replays every distinct snapshot through the frozen
  gates on the clean agent core.  Each replay becomes a standard
  proposal/experiment/node triple backdated to the snapshot's wall time, so
  the run-dir plots like any other ablation arm.

The session world is mounted FULLY read-write (unlike the standard executor,
whose /work is read-only outside src/): the whole point of this arm is that
the agent can build and bench itself.  Snapshots only ever stage src/, so
edits to frozen scripts/ or stray artifacts never enter a node, and replay
always runs the pristine harness.

Logging convention: tick lines deliberately avoid the ``elapsed=X.XXh``
pattern so ablation/plot.py's work-time reconstruction falls back to the
plain wall axis — correct here, because this arm has no dead gaps by
construction.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simpleevo.config import load_config
from simpleevo.db.store import Proposal, ResearchStore


def _log(msg: str) -> None:
    print(f"[cont-agent] {msg}", flush=True)


def _git(ws: Path, *args: str, env_extra: dict | None = None) -> str:
    env = {**os.environ, **(env_extra or {})}
    proc = subprocess.run(
        ["git", "-C", str(ws), *args], env=env,
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout.strip()


def snapshot_commit(ws: Path, parent_sha: str, tag: str) -> str | None:
    """Freeze the session's src/ into a side commit; None if unchanged.

    Uses a throwaway index so the worktree's own index/HEAD (owned by the
    harness, untouched by the agent) never moves.  Only ``src`` is staged:
    PROGRESS.md, build artifacts, and any edit to frozen scripts/ stay
    untracked and therefore never reach a replayed node.
    """
    with tempfile.NamedTemporaryFile(prefix="cont-agent-idx-") as tmp:
        env = {
            "GIT_INDEX_FILE": tmp.name,
            "GIT_AUTHOR_NAME": "cont-agent-snapshot",
            "GIT_AUTHOR_EMAIL": "cont-agent@harness",
            "GIT_COMMITTER_NAME": "cont-agent-snapshot",
            "GIT_COMMITTER_EMAIL": "cont-agent@harness",
        }
        _git(ws, "read-tree", parent_sha, env_extra=env)
        _git(ws, "add", "-A", "--", "src", env_extra=env)
        tree = _git(ws, "write-tree", env_extra=env)
        parent_tree = _git(ws, "rev-parse", f"{parent_sha}^{{tree}}")
        if tree == parent_tree:
            return None
        return _git(
            ws, "commit-tree", tree, "-p", parent_sha,
            "-m", f"cont-agent snapshot {tag}", env_extra=env,
        )


class ContAgentRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.config = load_config(Path(args.config))
        self.run_dir = Path(args.run_dir)
        self.t0 = time.monotonic()
        self.t0_wall = time.time()
        self.deadline = self.t0 + args.max_seconds
        self.snapshots: list[tuple[int, str, float, str]] = []  # (n, sha, wall, parent)
        self.agent_outcome: dict = {}
        self.invocation = "experiment-cont-agent"
        self.root_obj = 1.0
        self.objective_key = str(
            (self.config.metrics_schema.get("objective") or {}).get(
                "key", "OBJECTIVE"
            )
        )
        self.gate_keys = tuple(
            g["key"] for g in (self.config.metrics_schema or {}).get("gates") or []
            if g.get("key")
        )

    # -- session -----------------------------------------------------------

    def _session_world(self, ws_path: Path):
        """The agent's sandbox: the WHOLE session world read-write at /work.

        Unlike the standard executor (read-only /work, writable /work/src),
        this arm lets the agent build and bench in place — self-measurement
        is the point.  Snapshots stage src/ only, so nothing else it touches
        can reach a replayed node.
        """
        from simpleevo.runtime import ApptainerSandbox, executor_environment
        from simpleevo.contracts import (
            MountMode,
            MountSpec,
            SandboxSpec,
        )

        sandbox = ApptainerSandbox(userns=True)
        return sandbox.bind(
            SandboxSpec(
                image=self.config.runtime_image,
                environment=executor_environment(
                    environ={**os.environ, "BENCH_PIN": str(self.args.agent_core)},
                    base_url=self.config.executor.get("base_url"),
                    max_output_tokens=64000,
                ),
                network=True,
            ),
            mounts=(
                MountSpec(ws_path, PurePosixPath("/work"), MountMode.READ_WRITE),
                MountSpec(
                    self.provider.repo, PurePosixPath("/repo"), MountMode.READ_ONLY
                ),
            )
            + tuple(
                MountSpec(Path(p), PurePosixPath(p), MountMode.READ_ONLY)
                for p in self.config.read_only_binds
            ),
        )

    def _session_prompt(self, hours: float, baseline: float) -> str:
        from simpleevo.assistant.prompts import load_semantic

        semantic = load_semantic("executor", self.config.prompt_dir)
        if self.args.mode == "continuation":
            handover = self._handover_dossier()
            return f"""{semantic}

Task goal:
{self.config.goal}

Gates:
{self.config.gate_block}

You are taking over a world a previous engineer already improved. Their
handover report (what they did, how far they got, what they verified):

--- HANDOVER ---
{handover}
--- END HANDOVER ---

Your shift (the rules that differ from a normal executor run):
- The world you inherit measures lookups_per_sec ≈ {baseline / 1e6:.2f}M
  (≈{baseline:.0f}). The pristine baseline was 1.35M; the known-plausible
  cap is 8M. Other engineers have reached far beyond where this world now
  stands — there is a LOT of headroom left, by construction.
- You have {hours:.1f} hours. You are expected to work the ENTIRE shift.
  Finishing early is a failure mode, not a virtue: "gates pass, good enough"
  is exactly the mistake the previous engineer made. Bank each win, then go
  find the next one. When one direction saturates, switch mechanism, not
  just parameter.
- Work the way a single engineer actually would: read, form a hypothesis,
  change src/, build, measure, keep or revert, repeat. You can measure
  yourself at any time: bash scripts/check_verify.sh and bash scripts/bench.sh
  both work in your world (bench takes ~1-2 min).
- scripts/ and benchmarks/ are frozen harness. Edits there are discarded by
  the harness and would only fool you.
- Keep a shift log at PROGRESS.md (repo root, OUTSIDE src/): one line per
  measurement — rough time, what you changed, lookups_per_sec, keep/revert.
- Git staging and commits belong to the harness (read-only git like status,
  diff, log is fine). The harness freezes your src/ into snapshots while you
  work and evaluates them against the frozen gates; only gate-passing
  snapshots count.

When your shift ends — time up — emit your SELF_REPORT block (see the
protocol in your role brief) and stop.
"""
        return f"""{semantic}

Task goal:
{self.config.goal}

Gates:
{self.config.gate_block}

Starting point: the pristine baseline measures lookups_per_sec = {baseline:.0f}.

Your shift (the only rules that differ from a normal executor run):
- You are alone with this world for the next {hours:.1f} hours. Nothing is
  taken away between your steps — the repository persists for your whole
  shift. Work the way a single engineer actually would: read, form a
  hypothesis, change src/, build, measure, keep or revert, repeat.
- You can measure yourself at any time: bash scripts/check_verify.sh and
  bash scripts/bench.sh both work in your world (bench takes ~1-2 min).
- scripts/ and benchmarks/ are frozen harness. Edits there are discarded by
  the harness and would only fool you.
- Keep a shift log at PROGRESS.md (repo root, OUTSIDE src/): one line per
  measurement — rough time, what you changed, lookups_per_sec, keep/revert.
- Git staging and commits belong to the harness (read-only git like status,
  diff, log is fine). The harness freezes your src/ into snapshots while you
  work and evaluates them against the frozen gates; only gate-passing
  snapshots count.
- Pace yourself against the clock: deep rewrites that never build are worse
  than a ladder of measured small wins.

When your shift ends — time up, or you believe you are done — emit your
SELF_REPORT block (see the protocol in your role brief) and stop.
"""

    def _handover_dossier(self) -> str:
        """The previous shift's final SELF_REPORT, extracted from its trace."""
        import json

        trace = self.run_dir / "traces" / "experiment-cont-agent.jsonl"
        text = ""
        if trace.exists():
            for line in trace.read_text(errors="replace").splitlines():
                try:
                    outer = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if outer.get("event_type") != "raw_line":
                    continue
                try:
                    inner = json.loads(outer.get("payload") or "")
                except json.JSONDecodeError:
                    continue
                if inner.get("type") == "result" and isinstance(
                    inner.get("result"), str
                ):
                    text = inner["result"]
        return text[:6000] or "(previous shift report unavailable)"

    def run_session(self, ws_path: Path, baseline: float) -> None:
        from simpleevo.assistant.agent import Agent
        from simpleevo.trace.store import TraceStore

        timeout = max(60, int(self.deadline - time.monotonic()) - 30)
        hours_left = timeout / 3600.0
        effort = self.config.executor.get("effort")
        extra = ["--effort", str(effort)] if effort else []

        agent = Agent(
            world=self._session_world(ws_path),
            command="claude",
            timeout_seconds=timeout,
            # WebSearch/WebFetch parity with scientist seats: the web is
            # the sanctioned external-information entrance, and a
            # controlled comparison cannot give one arm a channel the
            # other lacks (2026-09-01 controlled-trial audit).
            allowed_tools="Read,Edit,Write,Bash,WebSearch,WebFetch",
            model=self.config.executor.get("model") or None,
            extra_args=extra,
            trace_store=TraceStore(self.run_dir),
            # Usage is reconstructed from the L1 trace after the session (see
            # _reconstruct_usage): the live recorder would only ever catch
            # the single final result event — one point on the cost curve,
            # and nothing at all when the session is killed at the wall cap.
            usage_observer=None,
            invocation_id=self.invocation,
            role="executor",
        )
        prompt = self._session_prompt(hours_left, baseline)
        try:
            text = agent.run_text(prompt, cwd=ws_path, label="cont-agent")
            self.agent_outcome = {"ok": True, "chars": len(text or "")}
        except Exception as exc:  # timeout / crash — snapshots still replay
            self.agent_outcome = {"ok": False, "error": str(exc)[:400]}

    # -- replay ------------------------------------------------------------

    def _reconstruct_usage(self) -> None:
        """Rebuild executor token accounting from the session's L1 trace.

        Every stream-json assistant event carries Anthropic-shaped per-call
        usage; summing them matches API billing and gives the cost axis one
        point per model call instead of a single end-of-session blob (and
        nothing at all when the session is killed at the wall cap).  Trace
        lines carry no per-event timestamps, so record timestamps are
        interpolated uniformly over the session span — an approximation
        noted in §12.
        """
        import json

        from simpleevo.trace.usage import extract_usage

        trace = self.run_dir / "traces" / f"{self.invocation}.jsonl"
        if not trace.exists():
            return
        events = []
        for line in trace.read_text(errors="replace").splitlines():
            try:
                outer = json.loads(line)
            except json.JSONDecodeError:
                continue
            if outer.get("event_type") != "raw_line":
                continue
            try:
                inner = json.loads(outer.get("payload") or "")
            except json.JSONDecodeError:
                continue
            if inner.get("type") != "assistant":
                continue
            usage = (inner.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                events.append(usage)
        if not events:
            return
        t_start = self.t0_wall
        t_end = time.time()
        span = max(1.0, t_end - t_start)
        out = self.run_dir / "telemetry" / "usage.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as stream:
            for i, usage in enumerate(events):
                tokens = extract_usage(usage)
                if not tokens:
                    continue
                record = {
                    "role": "executor",
                    "timestamp": t_start + span * (i + 1) / len(events),
                    **tokens,
                }
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
        _log(f"usage reconstructed from {len(events)} assistant events")

    def _replay_eval(self, sha: str, core: int):
        """Pristine-harness verify+bench of a snapshot commit on ``core``."""
        from simpleevo.runtime import (
            ApptainerSandbox,
            evaluator_environment,
        )
        from simpleevo.contracts import (
            EvaluationResult,
            MountMode,
            MountSpec,
            SandboxSpec,
            WorkspaceSpec,
        )
        from simpleevo.adjudicate.evaluator import run_eval
        from simpleevo.adjudicate.gate import GateSpec, apply_gates

        ws = self.provider.create(WorkspaceSpec(f"replay-{sha[:8]}", sha))
        try:
            sandbox = ApptainerSandbox(userns=True)
            builder = sandbox.bind(
                SandboxSpec(
                    image=self.config.runtime_image,
                    environment=evaluator_environment(
                        environ={**os.environ, "BENCH_PIN": str(core)}
                    ),
                    network=True,
                ),
                mounts=(
                    MountSpec(ws.path, PurePosixPath("/work"), MountMode.READ_WRITE),
                    MountSpec(
                        self.provider.repo, PurePosixPath("/repo"), MountMode.READ_ONLY
                    ),
                ),
            )
            result = run_eval(
                list(self.config.eval_commands),
                world=builder,
                metrics_schema=dict(self.config.metrics_schema),
                timeout_seconds=self.config.eval_timeout_seconds,
            )
            # run_eval returns the lean EvalResult; apply_gates expects the
            # contracts shape (with .error) — wrap exactly like the standard
            # ExperimentRunner does.
            evaluation = EvaluationResult(
                result.text, result.metrics, result.returncodes,
            )
            gate = apply_gates(
                evaluation, GateSpec(
                    objective_key=self.objective_key,
                    gate_keys=self.gate_keys,
                )
            )
            return evaluation, gate
        finally:
            self.provider.remove(ws)

    def _ingest_snapshot(self, episode_id, root_id, prev, sha, wall, metrics, gate):
        changed = [
            p for p in _git(
                self.provider.repo,
                "diff", "--name-only", prev["sha"], sha,
            ).splitlines() if p.strip()
        ]
        status = "completed" if gate.passed else "gate_rejected"
        with self.store.transaction() as tx:
            prop = Proposal(
                proposal_id=uuid.uuid4().hex,
                node_id=root_id,
                episode_id=episode_id,
                instruction="continuous coding-agent session (snapshot)",
                rationale={"arm": "coding-agent-cont"},
                status="done",
                created_at=wall,
            )
            tx.create_proposal(prop)
            exp_id = uuid.uuid4().hex
            tx.create_experiment(
                experiment_id=exp_id,
                proposal_id=prop.proposal_id,
                parent_node_id=prev["node_id"],
                status="running",
                created_at=wall,
            )
            tx.update_experiment_result(
                experiment_id=exp_id,
                result_sha=sha,
                metrics=metrics,
                gate_result=gate,
                status=status,
                changed_paths=changed,
            )
            node = tx.create_node(
                parent_node_id=prev["node_id"],
                experiment_id=exp_id,
                sha=sha,
                metrics=metrics,
                gate_result=gate,
                depth=prev["depth"] + 1,
                status="active",
                created_at=wall,
            )
            tx.link_experiment_child(exp_id, node.node_id)
        return node

    # -- driver ------------------------------------------------------------

    def run(self) -> int:
        from ablation.driver import _spend_usd
        from simpleevo.contracts import WorkspaceSpec
        from simpleevo.assistant.git_worktree import GitWorkspaceProvider
        from simpleevo.cli import _ensure_baseline_measured, _init_run
        from simpleevo.db.queries import ResearchQueries

        args = self.args
        self.run_dir.mkdir(parents=True, exist_ok=True)
        _log(f"init {self.run_dir}")
        _init_run(self.config, self.run_dir)
        self.store = ResearchStore(self.run_dir / "simpleevo.db")
        _ensure_baseline_measured(self.config, self.run_dir, self.store)

        queries = ResearchQueries(self.store.path)
        root = queries.root_node()
        root_obj = (root.metrics or {}).get(self.objective_key)
        self.root_obj = float(root_obj)

        # Shift 2+: continue from a given node (world sha + chain position).
        if args.parent_node:
            with self.store.transaction() as tx:
                parent = tx.get_node(args.parent_node)
            if parent is None:
                raise SystemExit(f"--parent-node {args.parent_node} not found")
            base_sha = args.base_sha or parent.sha
            prev = {
                "node_id": parent.node_id,
                "sha": base_sha,
                "depth": parent.depth,
            }
            world_obj = float(
                (parent.metrics or {}).get(self.objective_key) or root_obj
            )
        else:
            base_sha = args.base_sha or root.sha
            prev = {"node_id": root.node_id, "sha": base_sha, "depth": 0}
            world_obj = float(root_obj)
        _log(
            f"baseline {self.objective_key}={root_obj}; "
            f"shift-{args.mode} world starts at {world_obj:.0f} ({base_sha[:8]})"
        )

        self.invocation = (
            args.invocation
            or (
                "experiment-cont-agent-c2"
                if args.mode == "continuation"
                else "experiment-cont-agent"
            )
        )
        ws_name = (
            "cont-agent-session-c2"
            if args.mode == "continuation"
            else "cont-agent-session"
        )
        self.provider = GitWorkspaceProvider(self.run_dir, self.config.repo_path)
        ws = self.provider.create(WorkspaceSpec(ws_name, base_sha))
        _log(f"session world: {ws.path}")
        _log(f"shift log to tail: {ws.path / 'PROGRESS.md'}")

        with self.store.transaction() as tx:
            episode = tx.create_episode(
                inherited_from_episode_id=None, node_id=prev["node_id"],
            )
        episode_id = episode.episode_id

        thread = threading.Thread(
            target=self.run_session, args=(ws.path, world_obj), daemon=True
        )
        thread.start()
        n_snap = 0
        while True:
            # Sleep to the next tick; short ticks near the deadline.
            remain = self.deadline - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(args.snapshot_every, max(5.0, remain)))
            now_wall = time.time()
            t_h = (time.monotonic() - self.t0) / 3600.0
            spend = _spend_usd(self.run_dir, dict(self.config.pricing))
            if spend > args.budget_usd:
                _log(
                    f"t={t_h:.2f}h BUDGET BREACH ${spend:.2f} > "
                    f"${args.budget_usd:.2f} — session runs to wall cap"
                )
            try:
                sha = snapshot_commit(ws.path, prev["sha"], tag=f"{int(now_wall)}")
            except Exception as exc:
                _log(f"t={t_h:.2f}h snapshot failed: {exc}")
                continue
            if sha is None:
                _log(f"t={t_h:.2f}h snapshot: src/ unchanged since last")
            else:
                n_snap += 1
                self.snapshots.append((n_snap, sha, now_wall, prev["sha"]))
                _log(
                    f"t={t_h:.2f}h snapshot#{n_snap} {sha[:8]} "
                    f"(src changed; ${spend:.2f} spent)"
                )
                if args.live_every and n_snap % args.live_every == 0:
                    try:
                        evaluation, gate = self._replay_eval(sha, args.live_core)
                        val = (evaluation.metrics or {}).get(self.objective_key)
                        mult = (
                            f"{float(val) / self.root_obj:.2f}x"
                            if val is not None and self.root_obj else "?"
                        )
                        _log(
                            f"t={t_h:.2f}h LIVE snap#{n_snap}: {mult} "
                            f"gate={'PASS' if gate.passed else 'FAIL'} "
                            "(indicative, live core)"
                        )
                    except Exception as exc:
                        _log(f"t={t_h:.2f}h live replay failed: {str(exc)[:200]}")
            if not thread.is_alive():
                break

        thread.join(timeout=120)
        _log(f"session outcome: {self.agent_outcome}")
        self._reconstruct_usage()

        # Final freeze of whatever the shift ended on.
        try:
            final = snapshot_commit(ws.path, prev["sha"], tag="final")
            if final:
                n_snap += 1
                self.snapshots.append((n_snap, final, time.time(), prev["sha"]))
                _log(f"final snapshot#{n_snap} {final[:8]}")
        except Exception as exc:
            _log(f"final snapshot failed: {exc}")

        # Authoritative replay on the (now idle) agent core.
        best: tuple[float, int] | None = None
        for n, sha, wall, parent_sha in self.snapshots:
            # A mid-shift ingester may already have landed this snapshot as a
            # node (log shas are 8-char prefixes there — resolve nothing
            # here, self.snapshots holds full shas).  Skip what exists.
            with self.store.transaction() as tx:
                existing = tx.get_node_by_sha(sha)
            if existing is not None:
                prev = {
                    "node_id": existing.node_id,
                    "sha": sha,
                    "depth": existing.depth,
                }
                _log(f"replay snap#{n}: already ingested ({existing.node_id[:8]})")
                continue
            try:
                evaluation, gate = self._replay_eval(sha, args.agent_core)
            except Exception as exc:
                _log(f"replay snap#{n} infra-failed: {str(exc)[:200]}")
                continue
            metrics = dict(evaluation.metrics or {})
            val = metrics.get(self.objective_key)
            node = self._ingest_snapshot(
                episode_id, root.node_id, prev, sha, wall, metrics, gate
            )
            prev = {"node_id": node.node_id, "sha": sha, "depth": prev["depth"] + 1}
            mult = (
                float(val) / self.root_obj
                if val is not None and self.root_obj else None
            )
            if mult is not None and (best is None or mult > best[0]):
                best = (mult, n)
            _log(
                f"replay snap#{n}: {'?' if mult is None else f'{mult:.2f}x'} "
                f"gate={'PASS' if gate.passed else 'FAIL'} -> node "
                f"{node.node_id[:8]} (d{node.depth})"
            )

        spend = _spend_usd(self.run_dir, dict(self.config.pricing))
        elapsed = (time.monotonic() - self.t0) / 3600.0
        best_txt = "?" if best is None else f"{best[0]:.2f}x@snap{best[1]}"
        _log(
            f"done: snapshots={len(self.snapshots)} best={best_txt} "
            f"${spend:.2f} spent, {elapsed:.2f}h wall"
        )
        try:
            self.provider.remove(ws)
        except Exception:
            pass
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-cont-agent")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--max-seconds", type=float, default=14400.0)
    parser.add_argument("--budget-usd", type=float, default=30.0)
    parser.add_argument("--snapshot-every", type=float, default=300.0)
    parser.add_argument("--live-every", type=int, default=2,
                        help="live indicative replay every Nth distinct snapshot (0=off)")
    parser.add_argument("--mode", choices=("first", "continuation"),
                        default="first")
    parser.add_argument("--base-sha", default=None,
                        help="start world at this commit (default: root/parent sha)")
    parser.add_argument("--parent-node", default=None,
                        help="chain new snapshots onto this node (default: root)")
    parser.add_argument("--invocation", default=None)
    parser.add_argument("--agent-core", type=int, default=9)
    parser.add_argument("--live-core", type=int, default=11)
    args = parser.parse_args(argv)
    return ContAgentRunner(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
