"""Read-only sidecar: freeze a world's editable surface (src/) and the
harness body (.scientist) into timestamped snapshot dirs while an agent
works.

One snapshot per DISTINCT state (tree-hash dedup over src + body,
checked every ``--every`` seconds), so post-hoc replay through the
frozen gates yields the agent's true speedup-vs-wallclock curve —
independent of anything the agent claims — and the body freeze makes
any crash a minutes-scale loss for RESUME instead of a dead run (the
wire is the replay source; it lives in the body). Read-only on the
world; writes only under ``--out``.

Body copy rules: seat stdout streams (raw.txt) are skipped — they are
the large streams and the live transcripts live in the run's
claude-config anyway — and any file over 2 MB is skipped; everything
else (wire, session, memory, ledger, seat manifests/digests) is copied
per-file with torn mid-write reads tolerated (the next tick re-freezes;
wire is append-only JSONL whose torn tail a replay drops).

Stops at ``--max-seconds`` or when the world's ``.scientist/
conclusion.json`` appears (the scientist exit contract), whichever
comes first — then takes one final snapshot if the state is new.

Manifest: ``<out>/manifest.jsonl``, one line per snapshot:
    {"seq": 1, "wall_offset_s": 63.2, "unix_ts": ..., "tree": "ab12...",
     "body": "cd34..."}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

BODY_SKIP_NAMES = {"raw.txt"}
BODY_MAX_BYTES = 2_000_000


def _is_final(conclusion: Path) -> bool:
    """True unless the conclusion is a crash. A crashed conclusion is not
    the end of the world: run_scientist.sh's supervisor auto-resumes from
    wire.jsonl, so only deliver/abstain stop the sidecar. An unreadable
    conclusion keeps the old existence-only behavior (stop)."""
    try:
        return json.loads(conclusion.read_text()).get("outcome") != "crashed"
    except Exception:
        return True


def tree_state(src: Path) -> str:
    if not src.is_dir():
        return ""
    h = hashlib.sha256()
    for p in sorted(src.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(str(p.relative_to(src)).encode())
            h.update(str(st.st_size).encode())
            h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()


def body_state(body: Path) -> str:
    """Hash the freezable body — same shape as tree_state, minus the
    streams and oversized files the copy skips, so state and copy always
    agree on what a snapshot holds."""
    if not body.is_dir():
        return ""
    h = hashlib.sha256()
    for p in sorted(body.rglob("*")):
        if not p.is_file() or p.name in BODY_SKIP_NAMES:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > BODY_MAX_BYTES:
            continue
        h.update(str(p.relative_to(body)).encode())
        h.update(str(st.st_size).encode())
        h.update(str(st.st_mtime_ns).encode())
    return h.hexdigest()


def copy_body(body: Path, dest: Path) -> None:
    if not body.is_dir():
        return
    for p in sorted(body.rglob("*")):
        if not p.is_file() or p.name in BODY_SKIP_NAMES:
            continue
        try:
            if p.stat().st_size > BODY_MAX_BYTES:
                continue
            target = dest / p.relative_to(body)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        except OSError:
            continue  # torn/mid-write: the next tick re-freezes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sidecar src/ snapshotter for a lived-in world.")
    ap.add_argument("--world", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--subdir", default="src",
                    help="editable surface to watch (default: src)")
    ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--max-seconds", type=float, default=10800.0)
    args = ap.parse_args(argv)

    src = args.world / args.subdir
    body = args.world / ".scientist"
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"

    # resume-friendly numbering: continue after the last existing seq-NNN
    # so a relaunched loop (run_scientist.sh RESUME=1) appends instead of
    # overwriting the crashed attempt's history.
    seq = max((int(p.name[4:]) for p in args.out.glob("seq-[0-9][0-9][0-9]")),
              default=0)
    last: str | None = None
    crashed_noted = False
    t0 = time.monotonic()
    conclusion = args.world / ".scientist" / "conclusion.json"

    def take() -> None:
        nonlocal seq, last
        state = tree_state(src)
        bstate = body_state(body)
        combined = f"{state}|{bstate}"
        if combined == last:
            return
        seq += 1
        dest = args.out / f"seq-{seq:03d}"
        if dest.exists():  # never overwrite (restart safety)
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        copy_body(body, dest / "body")
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "seq": seq,
                "wall_offset_s": round(time.monotonic() - t0, 1),
                "unix_ts": time.time(),
                "tree": state[:16],
                "body": bstate[:16],
            }) + "\n")
        print(f"[snapshot] seq={seq} t={time.monotonic() - t0:.0f}s",
              flush=True)
        last = combined

    while True:
        if conclusion.exists() and _is_final(conclusion):
            print("[snapshot] final conclusion present — final check, "
                  "then stop", flush=True)
            take()
            break
        if conclusion.exists() and not crashed_noted:
            # crashed, not final: run_scientist.sh's supervisor may
            # resume the run from wire.jsonl. Keep watching (bounded by
            # --max-seconds; without a supervisor this branch costs
            # nothing beyond this one note).
            print("[snapshot] crashed conclusion present — supervisor "
                  "may resume, keep watching", flush=True)
            crashed_noted = True
        if time.monotonic() - t0 >= args.max_seconds:
            print("[snapshot] max-seconds reached — final check, "
                  "then stop", flush=True)
            take()
            break
        take()
        time.sleep(args.every)
    print(f"[snapshot] done: {seq} distinct states", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
