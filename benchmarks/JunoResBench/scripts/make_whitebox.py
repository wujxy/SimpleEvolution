"""Build a white-box task package from an existing blind package.

whitebox = blind + generator: the data files, scorer and metrics are
byte-identical to blind_task_<name>/, and the COMPLETE forward-model source
ships inside the package — the agent may read, run and modify it (generate
unlimited labeled data with any seed, build a forward likelihood, derive
features from the known CE(theta)/eps(r)/trigger model). Blind vs white-box
scores on the same test events are directly comparable; the only variable
is the information condition.

Fairness: no generation seed appears anywhere in the whitebox package
(blind files already strip it from every split's meta). The benchmark
packages use large random seeds, so regenerating the test truth by seed
search is computationally dead.

Usage:
  python3 scripts/make_whitebox.py --name electron
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH / "scripts"))
from make_benchmark import build_task_md  # noqa: E402

PREAMBLE = """## White-box setting

This is the white-box variant: the COMPLETE forward model that produced the
data ships with the package —

  - `juno_res_bench/`       the full detector simulator (numpy only;
                             stages 1-5: particle chain, photon generation,
                             optics, detection, electronics + trigger)
  - `generate_dataset.py`   command-line entry to generate labeled datasets

You may read, run and modify any of it. Typical uses:

  - generate unlimited synthetic events WITH ground truth under any seed of
    your choosing, to calibrate, validate or train your method;
  - build a forward likelihood and fit (E, vertex, t0) per test event;
  - derive features/weights from the known CE(theta), eps(r), per-PMT
    calibration and trigger models instead of estimating them from data.

The test set was produced by exactly this code with an unknown large random
seed (absent from this package; brute-force search is not the task).

train/val/test, the scorer and the metrics are byte-identical to the blind
variant — scores are directly comparable across the two.
"""


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True,
                    help="blind/whitebox package name (blind_task_<name> "
                         "must exist)")
    args = ap.parse_args()

    blind = BENCH / f"blind_task_{args.name}"
    white = BENCH / f"whitebox_task_{args.name}"
    if not (blind / "test.npz").exists():
        sys.exit(f"missing {blind}/test.npz — build the blind package first")

    white.mkdir(exist_ok=True)

    # 1. data + scorer: byte-identical copies from the blind package
    for f in ("train.npz", "val.npz", "test.npz", "evaluate.py"):
        data = (blind / f).read_bytes()
        (white / f).write_bytes(data)

    # 2. generator source (numpy-only, self-contained)
    src = white / "juno_res_bench"
    if src.exists():
        shutil.rmtree(src)
    shutil.copytree(BENCH / "juno_res_bench", src,
                    ignore=shutil.ignore_patterns("__pycache__"))

    # 3. local generate entry: same script, package-local imports
    gen = (BENCH / "scripts" / "generate_dataset.py").read_text()
    gen = gen.replace(
        "sys.path.insert(0, str(Path(__file__).resolve().parents[3]))",
        "sys.path.insert(0, str(Path(__file__).resolve().parent))")
    gen = gen.replace("from benchmarks.JunoResBench.juno_res_bench.",
                      "from juno_res_bench.")
    (white / "generate_dataset.py").write_text(gen)

    # 4. TASK.md = white-box preamble + the blind sheet body
    meta = json.loads(str(np.load(blind / "test.npz")["meta"]))
    body = build_task_md(meta["particle_type"], meta["mix"] or "1,1,1",
                         meta["max_wf_per_event"], meta)
    task = body.replace(
        "# JunoResBench — reconstruction task",
        "# JunoResBench — reconstruction task (white-box)", 1
    ).replace("## Readout", PREAMBLE + "\n## Readout", 1)
    (white / "TASK.md").write_text(task)

    # ---- self-checks -------------------------------------------------------
    ok = True
    for f in ("train.npz", "val.npz", "test.npz", "evaluate.py"):
        if sha256(blind / f) != sha256(white / f):
            print(f"FAIL byte identity: {f}")
            ok = False
    for split in ("train", "val", "test"):
        m = json.loads(str(np.load(white / f"{split}.npz")["meta"]))
        if m.get("seed") is not None:
            print(f"FAIL seed leak in {split}.npz meta")
            ok = False
    # simulator smoke: runs standalone from a foreign cwd (no repo paths)
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(
            [sys.executable, str(white / "generate_dataset.py"),
             "--events", "2", "--seed", "7", "--truth-only",
             "--out", str(Path(td) / "smoke.npz")],
            cwd=td, capture_output=True, text=True)
        if r.returncode != 0:
            print("FAIL simulator smoke run:\n", r.stderr[-2000:])
            ok = False
    # the smoke run leaves __pycache__ inside the shipped source — drop it
    for pc in white.glob("**/__pycache__"):
        shutil.rmtree(pc, ignore_errors=True)
    print("\n".join(
        f"{sha256(white / f)}  whitebox_task_{args.name}/{f}"
        for f in ("train.npz", "val.npz", "test.npz", "evaluate.py",
                  "TASK.md", "generate_dataset.py")))
    if not ok:
        sys.exit(1)
    print(f"built whitebox_task_{args.name}/ "
          f"(all self-checks passed)")


if __name__ == "__main__":
    main()
