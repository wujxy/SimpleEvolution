#!/usr/bin/env python3
"""Private release checks that call task evaluators only as subprocesses."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

import numpy as np


def hygiene_report(public_root, private_root):
    """Reject executable payloads from generated public/private data trees."""
    public_root = Path(public_root)
    private_root = Path(private_root)
    forbidden = (".py", ".pyc", ".so", ".sh")
    unexpected = []
    for label, root in (("public", public_root), ("private", private_root)):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in forbidden:
                unexpected.append(f"{label}/{path.relative_to(root)}")
    return {"pass": not unexpected, "unexpected_executables": sorted(unexpected)}


def physics_report(task_name, truth_path):
    """Check private particle truth without importing any evaluator."""
    with np.load(truth_path, allow_pickle=False) as data:
        truth = {key: data[key] for key in data.files}
    report = {}
    if {"step_offsets", "step_e_dep_mev", "evt_e_escape_mev", "evt_total_energy"} <= set(truth):
        deposited = np.add.reduceat(truth["step_e_dep_mev"], truth["step_offsets"][:-1])
        error = np.abs(deposited + truth["evt_e_escape_mev"] - truth["evt_total_energy"])
        report["energy_conservation_max_error_mev"] = float(error.max())
        report["energy_conservation_pass"] = bool(error.max() < 1e-8)
    if {"step_e_dep_mev", "step_e_vis_mev", "step_kinetic_mev"} <= set(truth):
        fraction = truth["step_e_vis_mev"] / np.maximum(truth["step_e_dep_mev"], 1e-12)
        kinetic = truth["step_kinetic_mev"]
        low = fraction[kinetic < 0.05]
        mid = fraction[(kinetic >= 0.5) & (kinetic <= 2.0)]
        report["low_energy_visible_fraction"] = float(low.mean()) if len(low) else None
        report["mid_energy_visible_fraction"] = float(mid.mean()) if len(mid) else None
        report["quenching_pass"] = bool(len(low) and len(mid) and low.mean() < mid.mean())
    if task_name == "ibd_positron_multisite":
        kind = truth["step_kind"]
        steps = truth["step_e_dep_mev"]
        offsets = truth["step_offsets"]
        annihilation = []
        for low, high in zip(offsets[:-1], offsets[1:]):
            selected = np.isin(kind[low:high], (3, 4, 5))
            annihilation.append(float(steps[low:high][selected].sum()))
        report["annihilation_mean_energy_mev"] = float(np.mean(annihilation))
        report["annihilation_pass"] = bool(abs(report["annihilation_mean_energy_mev"] - 1.021998) < 1e-6)
    return report


def evaluator_result(evaluator, private_root, public_root, submission):
    """Execute an evaluator process; no evaluator module is imported here."""
    completed = subprocess.run(
        [sys.executable, str(evaluator), "--private", str(private_root), "--public", str(public_root), "--submission", str(submission)],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode:
        return {"valid": False, "passed": False, "error": completed.stderr[-4096:]}
    return json.loads(completed.stdout)


def validate(task_name, public_root, private_root, evaluator, baseline, reference):
    """Return release status from data-only, physics, and evaluator checks."""
    hygiene = hygiene_report(public_root, private_root)
    physics = physics_report(task_name, Path(private_root) / "truth.npz")
    baseline_result = evaluator_result(evaluator, private_root, public_root, baseline)
    reference_result = evaluator_result(evaluator, private_root, public_root, reference)
    failures = []
    if not hygiene["pass"]:
        failures.append("dataset_contains_executable")
    for key, value in physics.items():
        if key.endswith("_pass") and not value:
            failures.append(key)
    if baseline_result.get("passed"):
        failures.append("public_baseline_reaches_target")
    if not reference_result.get("passed"):
        failures.append("reviewed_reference_misses_target")
    return {"release_ready": not failures, "failures": failures, "hygiene": hygiene, "physics": physics, "baseline": baseline_result, "reference": reference_result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--public", required=True)
    parser.add_argument("--private", required=True)
    parser.add_argument("--evaluator", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    report = validate(args.task, args.public, args.private, args.evaluator, args.baseline, args.reference)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["release_ready"] else 1)


if __name__ == "__main__":
    main()
