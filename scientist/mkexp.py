"""The seat-side experiment kit's backend: one command, one disposable world.

A cognitive seat that needs to MODIFY the world to test a claim runs the
``make-experiment`` script placed in its scratch; this module is what that
script calls. It is the same cheap fork a speculative executor gets —
small trees copied, data-scale directories symlinked into the read-only
originals — self-served, created by behavior at the moment it is needed
(Seat ≠ World: experiments get worlds, seats do not). The source world's
git baseline is recorded so the Scientist can read a seat's experiment
against the state it forked from.
"""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from .assistant_tools import _fork_world


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
    _fork_world(source, dest)

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
