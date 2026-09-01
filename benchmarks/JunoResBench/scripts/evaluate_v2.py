#!/usr/bin/env python3
"""Evaluate one energy-only JunoResBench v2 submission."""

import argparse
import json
from pathlib import Path

import numpy as np

from benchmarks.JunoResBench.juno_res_bench.resolution import score_v2


TRUTH_KEYS = {"evt_sample_role", "evt_e_true", "evt_e_vis"}


def load_inputs(truth_path, prediction_path):
    """Load and partition private truth and the sole submitted ``E_rec``."""
    with np.load(truth_path, allow_pickle=False) as truth:
        missing = TRUTH_KEYS - set(truth.files)
        if missing:
            raise ValueError(f"private truth is missing keys: {sorted(missing)}")
        role = np.asarray(truth["evt_sample_role"])
        kinetic = np.asarray(truth["evt_e_true"], dtype=float)
        visible = np.asarray(truth["evt_e_vis"], dtype=float)

    with np.load(prediction_path, allow_pickle=False) as prediction:
        if set(prediction.files) != {"E_rec"}:
            raise ValueError("prediction must contain exactly one array named E_rec")
        reconstructed = np.asarray(prediction["E_rec"], dtype=float)

    if any(array.ndim != 1 for array in (role, kinetic, visible, reconstructed)):
        raise ValueError("truth and prediction arrays must be one-dimensional")
    if not (len(role) == len(kinetic) == len(visible) == len(reconstructed)):
        raise ValueError("prediction length mismatch with private truth")
    if not np.isin(role, [0, 1]).all() or set(np.unique(role)) != {0, 1}:
        raise ValueError("evt_sample_role must contain probe=0 and control=1 rows")

    probe = role == 0
    control = role == 1
    return kinetic[probe], reconstructed[probe], visible[control], reconstructed[control]


def json_result(score):
    """Convert internal fractional resolution values to JSON percentages."""
    result = {
        "valid": score["valid"],
        "passed": score["passed"],
        "target_percent": 100.0 * score["target"],
    }
    if not score["valid"]:
        result["invalid_reasons"] = score["invalid_reasons"]
        return result

    result["points"] = [
        {
            "kinetic_mev": point["kinetic_mev"],
            "E_vis_mev": point["E_vis"],
            "sigma_mev": point["sigma"],
            "resolution_percent": 100.0 * point["resolution"],
        }
        for point in score["points"]
    ]
    result.update({
        "a_percent": 100.0 * score["a"],
        "b_percent": 100.0 * score["b"],
        "c_percent": 100.0 * score["c"],
        "R_1MeV_percent": 100.0 * score["R_1MeV"],
    })
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Score an energy-only JunoResBench v2 prediction"
    )
    parser.add_argument("--truth", required=True, help="private truth NPZ")
    parser.add_argument("--pred", required=True, help="prediction NPZ with E_rec")
    parser.add_argument("--out", help="optional score JSON output path")
    args = parser.parse_args()

    result = json_result(score_v2(*load_inputs(args.truth, args.pred)))
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered + "\n")


if __name__ == "__main__":
    main()
