"""Replay src/ snapshots through the frozen XSBench gates.

Row 0 is the pristine base repo itself (the harness-measured baseline);
rows 1..N are the snapshot dirs written by snapshot_world_loop.py, in
order. Each row: clean materialization of the base repo + overlay of
the snapshot's src/ + full rebuild + check_verify + bench. The judge
never trusts agent-reported numbers.

    python scripts/replay_xsbench.py \
        --snapshots runs/xsbench-3h/scientist/snapshots \
        --out runs/xsbench-3h/scientist/replay.csv

CSV columns: seq, wall_offset_s, verify, rate_plausible,
lookups_per_sec, bench_median_runtime_s
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
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns(".git"))
    if snap_src is not None:
        shutil.rmtree(dest / "src")
        shutil.copytree(snap_src, dest / "src")
    # Force a clean rebuild every time: stale objects with skewed mtimes
    # (copied trees) must never mask a source change.
    for p in (dest / "src").glob("*"):
        if p.suffix == ".o" or p.name == "XSBench":
            p.unlink()


def run_gate(workdir: Path, script: str, pin: int) -> tuple[int, str]:
    env = dict(os.environ, BENCH_PIN=str(pin))
    proc = subprocess.run(
        ["bash", f"scripts/{script}"], cwd=workdir, env=env,
        text=True, capture_output=True, timeout=900,
    )
    return proc.returncode, proc.stdout + "\n[stderr]\n" + proc.stderr


def parse(pattern: str, text: str) -> str:
    for line in text.splitlines():
        if line.startswith(pattern):
            return line[len(pattern):].strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-repo", type=Path,
                    default=Path("examples/xsbench_opt/repo"))
    ap.add_argument("--snapshots", required=True, type=Path,
                    help="dir containing seq-NNN/ + manifest.jsonl")
    ap.add_argument("--out", required=True, type=Path, help="CSV path")
    ap.add_argument("--pin", type=int, default=9)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=48,
                    help="cap on replayed snapshots (stride + last 3)")
    args = ap.parse_args(argv)

    base = args.base_repo.resolve()
    if not (base / "scripts" / "bench.sh").exists():
        print(f"error: {base} is not the xsbench repo", file=sys.stderr)
        return 1

    offsets = load_manifest(args.snapshots)
    snaps = sorted(
        (p for p in args.snapshots.glob("seq-*") if p.is_dir()),
        key=lambda p: int(p.name.split("-")[1]),
    )
    # Bound worst-case replay time (~1 min per row): stride-subsample
    # large snapshot sets, always keeping the last three — the delivery
    # endgame is where claims must be checked densest.
    if args.max_rows and len(snaps) > args.max_rows:
        stride = (len(snaps) + args.max_rows - 4) // (args.max_rows - 3)
        keep = sorted(set(snaps[::stride]) | set(snaps[-3:]),
                      key=lambda p: int(p.name.split("-")[1]))
        print(f"[replay] {len(snaps)} snapshots; subsampling every "
              f"{stride} + last 3 -> {len(keep)} rows", flush=True)
        snaps = keep
    rows = []

    def replay(seq: int, offset: float, snap_src: Path | None) -> None:
        with tempfile.TemporaryDirectory(prefix="xsreplay-") as td:
            workdir = Path(td) / "repo"
            materialize(base, snap_src, workdir)
            rc_v, out_v = run_gate(workdir, "check_verify.sh", args.pin)
            verify = "PASS" if rc_v == 0 else "FAIL"
            rc_b, out_b = run_gate(workdir, "bench.sh", args.pin)
            lps = parse("lookups_per_sec=", out_b)
            med = parse("bench_median_runtime_s=", out_b)
            rate = parse("RATE_PLAUSIBLE=", out_b)
            if not rate:  # bench crashed before the token
                rate = "FAIL" if rc_b != 0 else ""
            rows.append({
                "seq": seq,
                "wall_offset_s": offset,
                "verify": verify,
                "rate_plausible": rate,
                "lookups_per_sec": lps or "",
                "bench_median_runtime_s": med or "",
            })
            print(f"[replay] seq={seq} verify={verify} "
                  f"lps={lps or '-'} rate={rate or '-'}", flush=True)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
