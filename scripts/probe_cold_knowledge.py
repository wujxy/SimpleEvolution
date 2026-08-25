"""Cold-knowledge probe: ask the researcher model the L-moments trap
questions, bare — no world, no context, no tools.

Purpose: size the knowledge gap BEFORE building the example. A good
investigation demo needs the model's prior to be thin or confidently
wrong on exactly the facts the task requires, so that hand-rolling from
weights fails and consulting (claude + web) wins. What we measure here
is recall, not behaviour — the behavioural probe comes later, in-world.

Usage:
  python scripts/probe_cold_knowledge.py --spec runs/oneworld-demo-1/spec.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scientist.model_stdlib import build_stdlib_chat_model

# The L-moments battery came back FULL MARKS from deepseek-v4-flash (b1
# estimator verbatim, Gumbel/GUM fits, GEV solve, x100 exact) — that
# topic cannot test investigation. The battery below probes OTHER
# candidate traps: domains that are publicly documented (claude+web can
# get them) but plausibly thin in the weights.

BATTERY = [
    (
        "daycount_30e360_us",
        "Under the 30/360 US (ISDA 2006, Bond Basis) day count "
        "convention, how many days of accrual between: (a) 2024-01-31 "
        "and 2024-07-31; (b) 2024-02-29 and 2025-08-31; (c) 2024-08-31 "
        "and 2024-09-30? State each adjustment rule you applied.",
    ),
    (
        "act_act_icma",
        "A semiannual-coupon bond (5% annual rate, 100 nominal) has a "
        "coupon period 2023-12-31 to 2024-06-30 and settles 2024-02-15. "
        "Compute accrued interest under ACT/ACT (ICMA), showing the "
        "denominator calculation including the 2024 leap day.",
    ),
    (
        "gum_k_factor",
        "Per the GUM (JCGM 100:2008), a measurement has combined "
        "standard uncertainty uc = 0.021 with Welch-Satterthwaite "
        "effective degrees of freedom veff = 6.5. What coverage factor "
        "k gives approximately 95% coverage, and what is the expanded "
        "uncertainty U?",
    ),
    (
        "gps_ca_taps",
        "GPS C/A code generation: state the G1 and G2 generator "
        "polynomials (tap positions), and the G2 tap pair and chip delay "
        "for PRN 1 and PRN 19.",
    ),
    (
        "moonrise_horizon",
        "Computing moonrise/set times per standard almanac practice "
        "(Meeus): what geometric altitude of the Moon's center defines "
        "the moonrise horizon, and what are the standard constants "
        "(refraction, parallax fraction) in that expression?",
    ),
    (
        "mayan_correlation",
        "Mayan Long Count to Gregorian conversion: state the correlation "
        "constant in standard scholarly use, and convert 13.0.0.0.0 to "
        "its Gregorian date.",
    ),
]

QUESTIONS = [
    (
        "pwm_estimator",
        "Write down, precisely, the unbiased sample estimator b1 of the "
        "first probability-weighted moment (Hosking & Wallis 1997 "
        "convention, order statistics x_{1:n} <= ... <= x_{n:n}), and the "
        "relation giving the second L-moment lambda2 from b0 and b1.",
    ),
    (
        "gumbel_lmoment_fit",
        "Give the L-moment estimators for the Gumbel distribution's "
        "location xi and scale alpha in terms of the sample L-moments "
        "lambda1 and lambda2, and state the Gumbel's theoretical "
        "L-skewness tau3.",
    ),
    (
        "gev_shape_solve",
        "For the GEV distribution fitted by L-moments, write the equation "
        "relating the L-skewness tau3 to the shape parameter k, and "
        "describe how it is solved numerically in practice.",
    ),
    (
        "return_level_arith",
        "For a Gumbel distribution with xi = 100 and alpha = 20, compute "
        "the 100-year return level (non-exceedance probability F = 0.99). "
        "Show the formula you use and the final number.",
    ),
]


TIDES = [
    (
        "tide_prediction_equation",
        "Write the operational tide-height prediction equation used by "
        "NOAA CO-OPS from harmonic constituents (amplitude H, epoch g, "
        "speed omega): show exactly how the equilibrium argument V0+u "
        "and the nodal factor f enter, what time reference the argument "
        "uses, and how V0 is obtained for a given prediction date.",
    ),
    (
        "doodson_nodal_corrections",
        "For the tidal constituents M2 and K1: give their Doodson "
        "numbers, and the formula for the nodal correction factor f and "
        "phase correction u in terms of N, the longitude of the Moon's "
        "ascending node.",
    ),
    (
        "coops_api_fields",
        "NOAA CO-OPS provides per-station harmonic constituents via a "
        "web API. What is the endpoint, what fields does it return, and "
        "what datum are the amplitudes referenced to?",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe-cold-knowledge")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--set", dest="question_set",
                        choices=["lmoments", "battery", "tides"],
                        default="battery")
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    model_cfg = dict(spec.get("model") or {})
    # Cold-recall questions can burn the whole default budget on hidden
    # reasoning before any text lands; give the probe headroom.
    model_cfg["max_output_tokens"] = 16384
    model = build_stdlib_chat_model(model_cfg)

    questions = {
        "lmoments": QUESTIONS, "battery": BATTERY, "tides": TIDES,
    }[args.question_set]
    for name, question in questions:
        reply = model.complete(
            system="", messages=[{"role": "user", "content": question}],
            timeout_seconds=180, tools=None, json_object=False,
        )
        print("=" * 72)
        print(f"QUESTION={name}")
        print("-" * 72)
        print(reply.text.strip())
    print("=" * 72)
    print("(score against: b1 = (1/n) sum (j-1)/(n-1) x_{j:n};  "
          "lambda2 = 2*b1 - b0;  alpha = lambda2/ln2, xi = lambda1 - "
          "gamma*alpha;  tau3_Gumbel = ln2/ln3 = 0.16997;  GEV: tau3 = "
          "2(1-3^-k)/(1-2^-k) - 3, Newton with rational initial guess;  "
          "x100 = xi - alpha*ln(-ln 0.99) = 100 + 20*4.60015 = 192.003)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
