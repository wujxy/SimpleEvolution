"""The seat-side experiment kit's backend: one command, one disposable world.

A cognitive seat that needs to MODIFY the world to test a claim runs the
``make-experiment`` script placed in its scratch; this module is what that
script calls. It is the same cheap fork a speculative executor gets —
small trees copied, data-scale directories symlinked into the read-only
originals — self-served, created by behavior at the moment it is needed
(Seat ≠ World: experiments get worlds, seats do not). The source world's
git baseline is recorded so the Scientist can read a seat's experiment
against the state it forked from.

This module is deliberately self-contained — stdlib only, no
scientist-package imports. The kit COPIES it into the seat's scratch and
runs it there: a seat must not be handed the repository (harness prompts
included) just to fork a world. assistant_tools imports fork_world from
here for the speculative-executor prebuild — one fork implementation,
two callers.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

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


def fork_world(source: Path, dest: Path) -> None:
    """Disposable copy of a world — the one way an experiment world is
    made, whether pre-built for a speculative executor or self-served
    through a seat's make-experiment kit.

    Small trees (src/, scripts/, .git, docs) are copied so prototyping is
    free; data-scale directories — anything named ``benchmarks`` or 512 MB
    and up — become symlinks into the source, where the read-only mount
    rejects writes at the kernel level. The PI's records (``.scientist``)
    never ship: the private thought stream stays home, and the public
    knowledge layer (research memory) is reached by pointing seats at
    the live copy, not by packing it. (The include_ledger variant that
    once lived in assistant_tools shipped the record into a reviewer's
    fork — and, the seat home being inside .scientist/scratch, copied
    the destination into itself; the redesign reads the record directly
    instead.)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mkexp",
        description="Create a disposable experiment world from a source "
                    "world (the make-experiment kit's backend).",
    )
    parser.add_argument("--source", required=True, type=Path,
                        help="the world to fork (usually the live world)")
    parser.add_argument("--dest", required=True, type=Path,
                        help="the experiment directory to create")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    dest = args.dest.resolve()
    fork_world(source, dest)

    try:
        sha = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "(unversioned source)"
    (dest / "EXPERIMENT_BASELINE").write_text(
        f"{sha}\n{time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )
    print(f"experiment world ready at {dest} "
          f"(baseline {sha[:12] if sha.startswith('(') else sha[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
