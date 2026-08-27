#!/usr/bin/env python3
"""Build the waverec white-box task package from the blind package.

whitebox = blind + generator: data files, scorer and metrics are
byte-identical to blind_task/, and the complete waveform generator source
ships inside the package — the agent may read, run and modify it (generate
unlimited labeled data under any seed, build the exact matched filter from
the known pulse shape, fit pulse trains directly). Blind vs white-box
scores on the same test events are directly comparable; the only variable
is the information condition.

Fairness: the blind data files carry no meta/seed (asserted), and seed
search is not the task.

Usage:
    python3 scripts/make_whitebox.py
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parents[1]

PREAMBLE = """## White-box setting

This is the white-box variant: the COMPLETE forward model that produced the
data ships with the package —

  - `wavegen/`              the waveform generator (numpy only): pulse
                            shape, SPE charge spectrum, pile-up, baseline,
                            noise and digitization
  - `generate_dataset.py`   command-line entry to generate labeled
                            waveforms under any seed of your choosing

You may read, run and modify any of it — e.g. generate unlimited labeled
training data, build the exact matched-filter/deconvolution kernel from
the known pulse shape, or fit per-event pulse trains directly.

The test set was produced by exactly this code with an unknown seed
(absent from this package; brute-force seed search is not the task).

`data/`, the scorer and the metrics are byte-identical to the blind
variant — scores are directly comparable across the two.
"""

# surgical edits to the blind sheet (each must match exactly once)
TITLE_OLD = "# Blind waveform reconstruction task"
TITLE_NEW = "# White-box waveform reconstruction task"
UNDOCUMENTED_OLD = """The detector
response — pulse shape, charge spectrum, noise level, baseline — is
deliberately **not** documented: characterizing it from the provided data is
part of the task."""
UNDOCUMENTED_NEW = """The detector
response — pulse shape, charge spectrum, noise level, baseline — is fully
documented by the included `wavegen/` source: reading, running and modifying
the generator is part of the white-box toolkit."""


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blind", default="blind_task",
                    help="blind package dir (relative to the benchmark)")
    ap.add_argument("--out", default="whitebox_task",
                    help="whitebox package dir to build")
    args = ap.parse_args()

    blind = BENCH / args.blind
    white = BENCH / args.out
    if not (blind / "data" / "waverec_test.npz").exists():
        sys.exit(f"missing {blind}/data/waverec_test.npz")

    (white / "data").mkdir(parents=True, exist_ok=True)

    # 1. data + scorer: byte-identical copies from the blind package
    for f in ("waverec_train.npz", "waverec_val.npz", "waverec_test.npz"):
        (white / "data" / f).write_bytes((blind / "data" / f).read_bytes())
    (white / "evaluate.py").write_bytes((blind / "evaluate.py").read_bytes())

    # 2. generator source (numpy-only, self-contained)
    if (white / "wavegen").exists():
        shutil.rmtree(white / "wavegen")
    shutil.copytree(BENCH / "wavegen", white / "wavegen",
                    ignore=shutil.ignore_patterns("__pycache__"))

    # 3. local generate entry: same script, package-local import path
    gen = (BENCH / "scripts" / "generate_dataset.py").read_text()
    gen = gen.replace(
        "ROOT = pathlib.Path(__file__).resolve().parents[1]",
        "ROOT = pathlib.Path(__file__).resolve().parent")
    (white / "generate_dataset.py").write_text(gen)

    # 4. TASK.md = blind sheet + white-box preamble, un-blind the wording
    task = (blind / "TASK.md").read_text()
    for old, new in ((TITLE_OLD, TITLE_NEW), (UNDOCUMENTED_OLD,
                                              UNDOCUMENTED_NEW)):
        if task.count(old) != 1:
            sys.exit(f"blind TASK.md drifted: pattern not found exactly "
                     f"once:\n{old[:60]}...")
    task = task.replace(TITLE_OLD, TITLE_NEW).replace(UNDOCUMENTED_OLD,
                                                      UNDOCUMENTED_NEW)
    task = task.replace("## The data", PREAMBLE + "\n## The data", 1)
    (white / "TASK.md").write_text(task)

    # ---- self-checks -------------------------------------------------------
    ok = True
    for f in ("data/waverec_train.npz", "data/waverec_val.npz",
              "data/waverec_test.npz", "evaluate.py"):
        if sha256(blind / f) != sha256(white / f):
            print(f"FAIL byte identity: {f}")
            ok = False
    for f in ("waverec_train.npz", "waverec_val.npz",
              "waverec_test.npz"):
        if "meta" in np.load(white / "data" / f).files:
            print(f"FAIL meta (seed carrier) present in {f}")
            ok = False
    # generator smoke: runs standalone from a foreign cwd (no repo paths)
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, str(white / "generate_dataset.py"),
             "--out", str(Path(td) / "smoke.npz"),
             "--events", "5", "--seed", "1"],
            cwd=td, capture_output=True, text=True)
        if r.returncode != 0:
            print("FAIL generator smoke run:\n", r.stderr[-2000:])
            ok = False
    # the smoke run leaves __pycache__ inside the shipped source — drop it
    for pc in white.glob("**/__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    print("\n".join(
        f"{sha256(white / f)}  {args.out}/{f}"
        for f in ("data/waverec_train.npz", "data/waverec_val.npz",
                  "data/waverec_test.npz", "evaluate.py", "TASK.md",
                  "generate_dataset.py")))
    if not ok:
        return 1
    print(f"built {args.out}/ (all self-checks passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
