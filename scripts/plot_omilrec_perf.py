#!/usr/bin/env python3
"""OMILREC arms: SPEED_MS vs worktime.

None of the streams carry clocks (wire, seat transcripts, coding
traces), so every reading is placed by ANCHOR + INTERPOLATION:

- r1-scientist (v6): each engagement's digest mtime anchors the wire
  line where its report was delivered (located by the
  ``"collaborator_id": "<id>"`` JSON fragment — prose never takes that
  shape); the wire's own readings — the PI's gate runs, the official
  ratchet — interpolate between anchors by line index, from the run's
  first snapshot change to the wire's mtime. Seat readings anchor at
  their digest mtime.
- coding relay (r1->r2->r3 legs): trace.jsonl anchored on the git
  commits each leg made (exact times from the world repo) plus the
  leg's first snapshot change and the trace file's mtime (session
  end); readings interpolate by trace line index. Worktime sums the
  leg spans — idle gaps between relay legs are excluded.

The staircase (best-so-far) counts the readings that can move the
official needle: the PI's own gates and Executor work. Cognitive seats
(proposer/challenger/...) bench inside disposable forks; those land as
scatter only. Load pollution inflates readings, never deflates, so a
cumulative-min staircase is robust to it.

Only ``SPEED_MS=... ms/evt (100 events)`` readings count: the frozen
baseline convention; evtmax-10 probes read slower and are not
comparable.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs" / "singlenode"
READING_RE = re.compile(
    r"SPEED_MS=([0-9.]+)\s*ms/evt \(100 events\)")
EXPERT_MS = 171.8   # v1.12.0 re-measured 2026-08-31: 171.71/171.80/176.17
BASELINE_MS = 919.9


def stream_readings(path: Path) -> list[tuple[int, float]]:
    """(line_index, value) for every 100-event reading in a stream."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            for m in READING_RE.finditer(line):
                out.append((i, float(m.group(1))))
    return out


def interpolate(anchors: list[tuple[int, float]], idx: int) -> float | None:
    """Time at stream position ``idx`` from sorted (position, time)."""
    for (ia, ta), (ib, tb) in zip(anchors, anchors[1:]):
        if ia <= idx <= ib:
            frac = (idx - ia) / max(1, ib - ia)
            return ta + (tb - ta) * frac
    return None


def first_snapshot_ts(run: str) -> float:
    manifest = RUNS / run / "snapshots" / "manifest.jsonl"
    return json.loads(manifest.read_text().splitlines()[0])["unix_ts"]


# --- r1-scientist: engagement anchors + wire interpolation -------------------


def scientist_points(run: str) -> tuple[list[tuple[float, float]],
                                        list[tuple[float, float]]]:
    """(all readings for scatter, ratchet readings for the staircase)."""
    state = RUNS / run / "world" / ".scientist"
    base = state / "assistant"
    wire_p = state / "session" / "wire.jsonl"
    wire_text = wire_p.read_text(encoding="utf-8", errors="replace")
    anchors = [(0, first_snapshot_ts(run)),
               (wire_text.count("\n") + 1, wire_p.stat().st_mtime)]

    all_pts: list[tuple[float, float]] = []
    ratchet_pts: list[tuple[float, float]] = []
    engagements: list[tuple[float, str]] = []
    for d in sorted(base.iterdir()):
        raw_p = d / "raw.txt"
        if not raw_p.is_file():
            continue
        digest_p = d / "digest.json"
        role = ""
        t = raw_p.stat().st_mtime
        if digest_p.is_file():
            try:
                meta = json.loads(digest_p.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
            role = str(meta.get("role") or "")
            t = digest_p.stat().st_mtime
        engagements.append((t, d.name))
        for _idx, v in stream_readings(raw_p):
            all_pts.append((t, v))
            if role == "executor":
                ratchet_pts.append((t, v))

    # Anchor placement: the delivered report embeds the engagement id as
    # a JSON fragment — ESCAPED when the report is nested inside a
    # tool-result string (v6's shape: \"collaborator_id\": \"...\"), raw
    # in a structured record. The plain quoted probe matches neither
    # escaped form and the wire's readings then interpolate across the
    # whole run between the two endpoint anchors — a flat-then-cliff
    # artifact. Bare id is the last-resort probe (prose mentions too,
    # but v6 is synchronous: dispatch, work, and delivery cluster).
    # Forward-search keeps anchors monotonic in wire position.
    pos_from = 0
    for t, name in sorted(engagements):
        for probe in (f'"collaborator_id": "{name}"',
                      f'\\"collaborator_id\\": \\"{name}\\"',
                      name):
            pos = wire_text.find(probe, pos_from)
            if pos >= 0:
                anchors.append((wire_text.count("\n", 0, pos), t))
                pos_from = pos
                break

    anchors.sort()
    for idx, v in stream_readings(wire_p):
        t = interpolate(anchors, idx)
        if t is not None:
            all_pts.append((t, v))
            ratchet_pts.append((t, v))
    return sorted(all_pts), sorted(ratchet_pts)


# --- r5-scientist relay: official teeth = world commits ----------------------
#
# r5's wire carries ts on every line (0d9e7de) but holds no 100-event
# readings — the PI banked through executors, and the readings that
# moved the official needle ARE the world commits. Seat raws hold
# fuller numbers (including the 189-191 "悬读" the PI judged
# un-bankable) — those stay scatter, never staircase.


def relay_teeth(run: str) -> list[tuple[float, float]]:
    """(time, official SPEED_MS) per banked tooth, from commit messages;
    the final tooth takes the conclusion's pinned median."""
    world = RUNS / run / "world"
    out = subprocess.run(
        ["git", "-C", str(world), "log", "--format=%ct %s"],
        capture_output=True, text=True, check=True).stdout
    final_v = None
    concl = RUNS / run / "world" / ".scientist" / "conclusion.json"
    if concl.is_file():
        m = re.search(r"median is ([0-9.]+)ms",
                      concl.read_text(encoding="utf-8",
                                      errors="replace"))
        if m:
            final_v = float(m.group(1))
    rows = []            # oldest first: (ts, value_or_None, after_handoff)
    after = False
    for line in reversed(out.splitlines()):
        ts_s, subject = line.split(" ", 1)
        ts = float(ts_s)
        if "-> " in subject and " ms" in subject:   # relay handoff tooth
            m = re.search(r"-> ([0-9.]+) ms", subject)
            if m:
                rows.append((ts, float(m.group(1))))
                after = True
                continue
        m = re.search(r"\(([0-9.]+)ms/", subject)   # banked tooth w/ value
        if m:
            rows.append((ts, float(m.group(1))))
        elif after and final_v is not None:
            rows.append((ts, None))                 # final teeth, no value
    # collapse trailing valueless teeth onto the LAST one: the pinned
    # median belongs where the dust settled, not at every silent commit
    last_fill = max((i for i, r in enumerate(rows)
                     if r[1] is None), default=None)
    teeth = [(ts, v) for i, (ts, v) in enumerate(rows)
             if v is not None or i == last_fill]
    return [(ts, final_v if v is None else v) for ts, v in teeth]


def relay_scatter(run: str) -> list[tuple[float, float]]:
    """Seat readings for scatter, anchored at each digest's mtime."""
    base = RUNS / run / "world" / ".scientist" / "assistant"
    pts: list[tuple[float, float]] = []
    for d in sorted(base.iterdir()):
        raw_p = d / "raw.txt"
        if not raw_p.is_file():
            continue
        digest_p = d / "digest.json"
        t = raw_p.stat().st_mtime
        if digest_p.is_file():
            t = digest_p.stat().st_mtime
        pts += [(t, v) for _i, v in stream_readings(raw_p)]
    return sorted(pts)


# --- coding relay: commit anchors + index interpolation ----------------------


def leg_commits(world: Path, t_start: float,
                t_end: float) -> list[tuple[str, float]]:
    """Short hashes of commits made during the leg, oldest first."""
    out = subprocess.run(
        ["git", "-C", str(world), "log", "--format=%ct %h"],
        capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        ts, short = line.split()
        if t_start <= float(ts) <= t_end + 600:
            rows.append((short, float(ts)))
    return list(reversed(rows))   # git log is newest-first


def coding_leg_points(run: str) -> tuple[list[tuple[float, float]],
                                         float, float]:
    """Anchored readings for one leg + its (start, end) wall times."""
    rd = RUNS / run
    t_start = first_snapshot_ts(run)
    t_end = (rd / "trace.jsonl").stat().st_mtime
    trace_p = rd / "trace.jsonl"
    rows = stream_readings(trace_p)
    text = trace_p.read_text(encoding="utf-8", errors="replace")
    n_lines = text.count("\n") + 1

    anchors = [(0, t_start), (n_lines, t_end)]
    search_from = 0
    for short, ts in leg_commits(rd / "world", t_start, t_end):
        pos = text.find(short, search_from)
        if pos < 0:
            continue
        anchors.append((text.count("\n", 0, pos), ts))
        search_from = pos + 7
    anchors.sort()

    pts = []
    for idx, v in rows:
        t = interpolate(anchors, idx)
        if t is not None:
            pts.append((t, v))
    return sorted(pts), t_start, t_end


def staircase(pts: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    ts, vs = [], []
    best = None
    for t, v in pts:
        if best is None or v < best:
            best = v
        ts.append(t)
        vs.append(best)
    return ts, vs


def main() -> None:
    now = time.time()

    sci_all, sci_ratchet = scientist_points("omilrec-v100-r1-scientist")
    t0 = min(t for t, _ in sci_all)
    sci_all = [(t - t0, v) for t, v in sci_all if t <= now]
    sci_ratchet = [(t - t0, v) for t, v in sci_ratchet if t <= now]
    sci_span = (RUNS / "omilrec-v100-r1-scientist" / "world" / ".scientist"
                / "session" / "wire.jsonl").stat().st_mtime - t0

    # r5 relay: teeth from world commits, seat readings as scatter;
    # spliced after r1's span (idle gap excluded, like the coding legs)
    r5_run = "omilrec-v100-r5-scientist"
    r5_t0 = first_snapshot_ts(r5_run)
    r5_teeth = [(t - r5_t0, v) for t, v in relay_teeth(r5_run) if t <= now]
    r5_scatter = [(t, v) for t, v in relay_scatter(r5_run) if t <= now]
    sci_all += [(sci_span + t - r5_t0, v) for t, v in r5_scatter]
    sci_ratchet += [(sci_span + t, v) for t, v in r5_teeth]
    sci_relay_mark = sci_span / 3600.0

    legs = ["omilrec-v100-r1-coding", "omilrec-v100-r2-coding",
            "omilrec-v100-r3-coding"]
    code_pts = []
    offset = 0.0
    leg_marks = []
    for run in legs:
        pts, t_start, t_end = coding_leg_points(run)
        span = min(t_end, now) - t_start
        code_pts += [(offset + t - t_start, v) for t, v in pts
                     if t_start <= t <= min(t_end, now)]
        leg_marks.append((offset / 3600.0, run.split("-v100-")[1]))
        offset += span
    code_pts.sort()

    fig, ax = plt.subplots(figsize=(10, 6))

    if sci_all:
        ax.scatter([t / 3600.0 for t, _ in sci_all],
                   [v for _, v in sci_all], s=9, alpha=0.25, color="#1f77b4")
        st, sv = staircase(sci_ratchet)
        ax.step([t / 3600.0 for t in st], sv, where="post",
                color="#1f77b4", lw=2,
                label=f"scientist agent (r1→r5 relay) — best {min(sv):.1f} ms"
                      f" ({len(sci_all)} readings)")
        ax.axvline(sci_relay_mark, color="#1f77b4", lw=0.7, ls=":",
                   alpha=0.6)
    if code_pts:
        ax.scatter([t / 3600.0 for t, _ in code_pts],
                   [v for _, v in code_pts], s=9, alpha=0.25, color="#ff7f0e")
        st, sv = staircase(code_pts)
        ax.step([t / 3600.0 for t in st], sv, where="post",
                color="#ff7f0e", lw=2,
                label=f"coding agent (3-leg relay) — best {min(sv):.1f} ms"
                      f" ({len(code_pts)} readings)")
        for x, _name in leg_marks[1:]:
            ax.axvline(x, color="#ff7f0e", lw=0.7, ls=":", alpha=0.6)

    ax.axhline(EXPERT_MS, color="#d62728", ls="--", lw=1.4,
               label=f"human expert {EXPERT_MS:.0f} ms")
    ax.axhline(BASELINE_MS, color="#7f7f7f", ls="-", lw=0.9, alpha=0.6,
               label=f"frozen baseline {BASELINE_MS:.1f} ms")

    ax.set_xlabel("worktime (h) — relay legs spliced, idle gaps excluded")
    ax.set_ylabel("SPEED_MS (ms/evt, 100-event readings)")
    ax.set_title("OMILREC v1.0.0 — SPEED_MS vs worktime")
    ax.set_ylim(150, 1000)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right")

    out = RUNS / "omilrec-perf-vs-worktime.png"
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print(f"wrote {out}")
    print(f"scientist relay: {len(sci_all)} readings "
          f"(staircase on {len(sci_ratchet)}), "
          f"span {max(t for t, _ in sci_all) / 3600:.1f} h; "
          f"r5 teeth: {[v for _, v in r5_teeth]}")
    print(f"coding relay: {len(code_pts)} readings, "
          f"span {offset / 3600:.1f} h")


if __name__ == "__main__":
    sys.exit(main())
