"""Replay src/ snapshots through the frozen JunoResBench white-box gates,
then judge the final state on the held-out test set.

Row 0 is the pristine base repo itself (the harness-measured baseline);
rows 1..N are the snapshot dirs written by snapshot_world_loop.py, in
order. Each row: clean materialization of the base repo + overlay of the
snapshot's src/ + check_verify + bench (val objective). The judge never
trusts agent-reported numbers.

After the snapshot rows, the SAME materialization is judged on the
held-out test split: the solver runs on test.npz (which carries no truth)
and is scored against the host-side truth npz the container never sees.
That test score — not the val score — is the benchmark result.

    PATH-prefix note: the world's scripts call ``python3``; inside the
    run container that is the image python (3.9 + numpy). This host
    replay prefixes --venv onto PATH so ``python3`` is a numpy python
    here too.

    python scripts/replay_jrb_wb.py \
        --snapshots runs/singlenode/<run>/snapshots \
        --out runs/singlenode/<run>/replay.csv

CSV columns: seq, wall_offset_s, verify, sanity, energy_res,
energy_bias, vertex_res_cm, timing_res_ns
Test judgement: <out>.test.json (baseline vs final, full score blobs).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_manifest(snap_root: Path) -> dict[int, float]:
    offsets: dict[int, float] = {}
    mf = snap_root / "manifest.jsonl"
    if not mf.exists():
        return offsets
    for line in mf.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row.get("seq"), int):
            offsets[row["seq"]] = float(row.get("wall_offset_s") or 0.0)
    return offsets


def materialize(base: Path, snap_src: Path | None, dest: Path) -> None:
    # hardlink the frozen side (the multi-GB task package never changes;
    # only src/ is per-row state) — instant rows for full-readout repos.
    # Requires the tempdir to share a filesystem with the base repo.
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns(".git"),
                    copy_function=os.link)
    if snap_src is not None:
        shutil.rmtree(dest / "src")
        shutil.copytree(snap_src, dest / "src")


def run_gate(workdir: Path, script: str, env: dict) -> tuple[int, str]:
    proc = subprocess.run(
        ["bash", f"scripts/{script}"], cwd=workdir, env=env,
        text=True, capture_output=True, timeout=1800,
    )
    return proc.returncode, proc.stdout + "\n[stderr]\n" + proc.stderr


def parse(pattern: str, text: str) -> str:
    for line in text.splitlines():
        if line.startswith(pattern):
            return line[len(pattern):].strip()
    return ""


def judge_test(workdir: Path, truth: Path, env: dict) -> dict:
    """Run the solver on the test split, score against the hidden truth."""
    bench = workdir / "benchmarks"
    pkgs = sorted(bench.glob("whitebox_task_*")) if bench.is_dir() else []
    if len(pkgs) != 1:
        return {"error": f"expected one whitebox_task_* in {bench}, "
                         f"found {[p.name for p in pkgs]}"}
    pkg = pkgs[0]
    # dir-format splits (full-readout era) or legacy single npz files
    def split(name: str) -> Path:
        p = pkg / name
        return p if p.is_dir() else pkg / f"{name}.npz"

    with tempfile.TemporaryDirectory(prefix="jrbtest-") as td:
        pred = Path(td) / "pred_test.npz"
        score = Path(td) / "score.json"
        proc = subprocess.run(
            ["python3", "src/solve.py",
             "--data", str(split("test")),
             "--train", str(split("train")),
             "--out", str(pred)],
            cwd=workdir, env=env, text=True, capture_output=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            return {"error": "solver failed on test.npz",
                    "tail": (proc.stdout + proc.stderr)[-800:]}
        proc = subprocess.run(
            ["python3", str(pkg / "evaluate.py"),
             "--data", str(truth), "--pred", str(pred),
             "--out", str(score)],
            env=env, text=True, capture_output=True, timeout=600,
        )
        if proc.returncode != 0:
            return {"error": "evaluate failed",
                    "tail": (proc.stdout + proc.stderr)[-800:]}
        return json.loads(score.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-repo", type=Path,
                    default=REPO_ROOT / "examples/junoresbench_wb_opt/repo")
    ap.add_argument("--snapshots", required=True, type=Path,
                    help="dir containing seq-NNN/ + manifest.jsonl")
    ap.add_argument("--out", required=True, type=Path, help="CSV path")
    ap.add_argument("--truth", type=Path,
                    default=REPO_ROOT / "benchmarks/JunoResBench/"
                    "blind_truth_electron/test_full.npz",
                    help="host-side test truth (never in-container)")
    ap.add_argument("--venv", type=Path,
                    default=Path("/datafs/users/wujxy/py_venv/my_env/bin"),
                    help="numpy-python bin dir to prefix onto PATH")
    ap.add_argument("--max-rows", type=int, default=48,
                    help="cap on replayed snapshots (stride + last 3)")
    ap.add_argument("--final-src", type=Path, default=None,
                    help="judge this src/ on test instead of the last "
                    "snapshot (e.g. RUN_DIR/world/src)")
    ap.add_argument("--skip-test", action="store_true",
                    help="val replay only, no held-out judgement")
    args = ap.parse_args(argv)

    base = args.base_repo.resolve()
    truth = args.truth.resolve()
    if not (base / "scripts" / "bench.sh").exists():
        print(f"error: {base} is not the jrb whitebox repo", file=sys.stderr)
        return 1
    # hardlink materialization needs a tempdir on the same filesystem as
    # the base repo (see materialize)
    tmp_root = REPO_ROOT / "runs" / ".replay_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = tmp_root
    env = dict(os.environ,
               PATH=f"{args.venv}:{os.environ.get('PATH', '')}",
               PYTHONDONTWRITEBYTECODE="1")

    offsets = load_manifest(args.snapshots)
    snaps = sorted(
        (p for p in args.snapshots.glob("seq-*") if p.is_dir()),
        key=lambda p: int(p.name.split("-")[1]),
    )
    if args.max_rows and len(snaps) > args.max_rows:
        stride = (len(snaps) + args.max_rows - 4) // (args.max_rows - 3)
        keep = sorted(set(snaps[::stride]) | set(snaps[-3:]),
                      key=lambda p: int(p.name.split("-")[1]))
        print(f"[replay] {len(snaps)} snapshots; subsampling every "
              f"{stride} + last 3 -> {len(keep)} rows", flush=True)
        snaps = keep
    rows = []

    def replay(seq: int, offset: float, snap_src: Path | None) -> None:
        with tempfile.TemporaryDirectory(prefix="jrbreplay-") as td:
            workdir = Path(td) / "repo"
            materialize(base, snap_src, workdir)
            rc_v, out_v = run_gate(workdir, "check_verify.sh", env)
            verify = "PASS" if rc_v == 0 else "FAIL"
            rc_b, out_b = run_gate(workdir, "bench.sh", env)
            sanity = parse("SANITY=", out_b) or (
                "FAIL" if rc_b != 0 else "")
            rows.append({
                "seq": seq,
                "wall_offset_s": offset,
                "verify": verify,
                "sanity": sanity,
                "energy_res": parse("energy_res=", out_b),
                "energy_bias": parse("energy_bias=", out_b),
                "vertex_res_cm": parse("vertex_res_cm=", out_b),
                "timing_res_ns": parse("timing_res_ns=", out_b),
            })
            print(f"[replay] seq={seq} verify={verify} sanity={sanity} "
                  f"E={rows[-1]['energy_res'] or '-'} "
                  f"V={rows[-1]['vertex_res_cm'] or '-'} "
                  f"T={rows[-1]['timing_res_ns'] or '-'}", flush=True)
            if rc_v != 0 or rc_b != 0:
                tail = (out_v + out_b)[-600:]
                print(f"[replay] seq={seq} non-zero exit; tail:\n{tail}",
                      flush=True)

    # Row 0: the pristine baseline, measured by the harness.
    replay(0, 0.0, None)
    for snap in snaps:
        seq = int(snap.name.split("-")[1])
        replay(seq, offsets.get(seq, float("nan")), snap)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"[replay] {len(rows)} rows -> {args.out}", flush=True)

    if args.skip_test or not truth.exists():
        if not args.skip_test:
            print(f"[judge] truth not found at {truth}; skipping test",
                  file=sys.stderr)
        return 0

    # Held-out judgement: pristine baseline + the final state.
    final_src = (args.final_src or (snaps[-1] if snaps else None))
    if final_src is None:
        print("[judge] no snapshots and no --final-src; nothing to judge",
              file=sys.stderr)
        return 0
    judgement: dict = {"truth": str(truth), "final_src": str(final_src)}
    with tempfile.TemporaryDirectory(prefix="jrbjudge-") as td:
        base_dir = Path(td) / "base"
        final_dir = Path(td) / "final"
        materialize(base, None, base_dir)
        materialize(base, final_src, final_dir)
        print("[judge] scoring pristine baseline on test...", flush=True)
        judgement["baseline"] = judge_test(base_dir, truth, env)
        print("[judge] scoring final state on test...", flush=True)
        judgement["final"] = judge_test(final_dir, truth, env)
    for who in ("baseline", "final"):
        s = judgement[who]
        if "error" in s:
            print(f"[judge] {who}: ERROR — {s['error']}", flush=True)
        else:
            print(f"[judge] {who}: E_res={s['energy']['resolution']:.4f} "
                  f"bias={s['energy']['bias']:+.4f} "
                  f"vertex={s['vertex']['res_68_cm']:.1f} cm "
                  f"timing={s['timing']['resolution_ns']:.2f} ns",
                  flush=True)
    test_out = args.out.with_suffix(".test.json")
    test_out.write_text(
        json.dumps(judgement, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"[judge] test judgement -> {test_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
