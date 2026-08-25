"""Grade a tide-research answers.json against the frozen official curve.

Harness-side, outside the world: the seat never sees this file or the
frozen values it reads. Stdlib only.

Usage:
  python3 grade.py --answers <world>/answers.json [--reference <this-dir>]

Prints the metric lines and GATE=PASS / GATE=FAIL; exit code mirrors
the gate.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timedelta
from pathlib import Path

RMS_MAX = 0.06        # demeaned RMS error, meters
MAX_MAX = 0.15        # demeaned max |error|, meters
HILO_MEDIAN_MAX = 10  # minutes, median over matched extrema
HILO_Q80_MAX = 25     # minutes, 80th percentile (flat nighttime lows make
                      # an absolute max ill-conditioned: a 1-cm-deep pan
                      # relocates its extremum by half an hour under
                      # centimeter-level curve differences)
HALF_WINDOW = 5       # extremum detection half-window, samples (30 min)
EXT_MERGE = timedelta(minutes=60)  # merge plateau extrema closer than this


def _parse_t(s: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp {s!r} (want "
                     "'2025-06-10T00:00' or '2025-06-10 00:00')")


def _load_reference(path: Path) -> tuple[list[datetime], list[float]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    preds = doc["predictions"]
    return ([_parse_t(p["t"]) for p in preds],
            [float(p["v"]) for p in preds])


def _load_answers(path: Path) -> tuple[list[datetime], list[float]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    preds = doc.get("predictions")
    if not isinstance(preds, list) or not preds:
        raise ValueError("answers.json has no 'predictions' list")
    return ([_parse_t(p["t"]) for p in preds],
            [float(p["v"]) for p in preds])


def _extrema(times: list[datetime], vals: list[float]):
    """Times of local highs and lows on the 6-min grid, with parabolic
    sub-sample refinement of the extremum time (the 6-min grid alone
    quantizes times by ±3 min; flat extrema on a mixed tide deserve
    better than that)."""
    def refined_time(i):
        y0, y1, y2 = vals[i - 1], vals[i], vals[i + 1]
        denom = y0 - 2 * y1 + y2
        shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
        shift = max(-1.0, min(1.0, shift))
        return times[i] + timedelta(minutes=6 * shift)

    n = len(vals)
    highs, lows = [], []
    for i in range(HALF_WINDOW, n - HALF_WINDOW):
        w = vals[i - HALF_WINDOW:i + HALF_WINDOW + 1]
        if vals[i] == max(w) and vals[i] > min(w):
            if not highs or times[i] - highs[-1][0] > EXT_MERGE:
                highs.append((refined_time(i), vals[i]))
        elif vals[i] == min(w) and vals[i] < max(w):
            if not lows or times[i] - lows[-1][0] > EXT_MERGE:
                lows.append((refined_time(i), vals[i]))
    return highs, lows


def _match_dt(a_times, b_times) -> list[float]:
    """Nearest-match time deltas (minutes) between two extremum lists."""
    out = []
    used = set()
    for ta in a_times:
        best_j, best = None, None
        for j, tb in enumerate(b_times):
            if j in used:
                continue
            d = abs((ta - tb).total_seconds()) / 60.0
            if best is None or d < best:
                best, best_j = d, j
        if best is not None and best <= 60:
            used.add(best_j)
            out.append(best)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True, type=Path)
    ap.add_argument("--reference", type=Path, default=Path(__file__).parent)
    args = ap.parse_args()

    ref_file = args.reference / "official-predictions-20250610-16.json"
    rt, rv = _load_reference(ref_file)
    try:
        at, av = _load_answers(args.answers)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"TIDE_POINTS=0")
        print(f"GATE=FAIL (answers unusable: {exc})")
        return 1

    print(f"TIDE_POINTS={len(at)} (expected {len(rv)})")
    if len(at) != len(rv):
        print("GATE=FAIL (prediction count/spacing does not cover the "
              "window at 6-minute resolution)")
        return 1
    drift = max((a - b for a, b in zip(at, rt)),
                key=lambda d: abs(d.total_seconds()))
    if abs(drift.total_seconds()) > 60:
        print(f"GATE=FAIL (timestamps misaligned by {drift})")
        return 1

    # demeaned error metrics
    ar, arv = sum(av) / len(av), sum(rv) / len(rv)
    dev = [a - r for a, r in zip(av, rv)]
    off = sum(dev) / len(dev)
    dem = [d - off for d in dev]
    rms = math.sqrt(sum(d * d for d in dem) / len(dem))
    mx = max(abs(d) for d in dem)
    num = sum((a - ar) * (r - arv) for a, r in zip(av, rv))
    den = math.sqrt(sum((a - ar) ** 2 for a in av)
                    * sum((r - arv) ** 2 for r in rv))
    corr = num / den if den else 0.0

    r_hi, r_lo = _extrema(rt, rv)
    a_hi, a_lo = _extrema(at, av)
    dts = _match_dt([t for t, _ in r_hi], [t for t, _ in a_hi])
    dts += _match_dt([t for t, _ in r_lo], [t for t, _ in a_lo])
    med = statistics.median(dts) if dts else float("inf")
    q80 = (sorted(dts)[max(0, math.ceil(0.8 * len(dts)) - 1)]
           if dts else float("inf"))
    worst = max(dts) if dts else float("inf")

    print(f"TIDE_OFFSET={off:+.4f} m (constant part, forgiven)")
    print(f"TIDE_RMS={rms:.4f} m")
    print(f"TIDE_MAX={mx:.4f} m")
    print(f"TIDE_CORR={corr:+.4f}")
    print(f"HILO_N={len(dts)} ref_high={len(r_hi)} ref_low={len(r_lo)}")
    print(f"HILO_MEDIAN_MIN={med:.1f}")
    print(f"HILO_Q80_MIN={q80:.1f}")
    print(f"HILO_MAX_MIN={worst:.1f} (reported; flat lows, not gated)")

    ok = (rms <= RMS_MAX and mx <= MAX_MAX
          and med <= HILO_MEDIAN_MAX and q80 <= HILO_Q80_MAX)
    print("GATE=PASS" if ok else "GATE=FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
