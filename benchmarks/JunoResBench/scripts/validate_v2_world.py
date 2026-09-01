#!/usr/bin/env python3
"""Release gate for a JunoResBench v2 world.

Validates, from the private truth and two online submissions, that the
world is physically sound, that no private key leaks into the public
package, that the 3.0% decision boundary is statistically stable, that a
reviewed waveform-only reference reaches the target, and that the public
charge baseline does not. Exits nonzero when any release condition fails.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.juno_res_bench.resolution import (
    PROBE_KINETIC_MEV,
    TARGET_R_1MEV,
    fit_peak,
    fit_resolution_curve,
    score_v2,
)
from benchmarks.JunoResBench.scripts import evaluate_v2


ANNIHILATION_MEV = 1.021998
ANNIHILATION_KINDS = (3, 4, 5)
FORBIDDEN_METADATA_KEYS = ("seed", "detector_config", "energy_grid")
PUBLIC_NPZ_KEYS = {
    "detector_geometry.npz": {"pmt_positions_m"},
    "calibration/labels.npz": {"source_energy_mev", "deployment_position_m"},
    "dev/truth.npz": {"evt_sample_role", "evt_e_true", "evt_e_vis"},
}
PUBLIC_FILES = {
    "TASK.md",
    "baseline.py",
    "detector_geometry.npz",
    "evaluate.py",
    "submission_api.py",
    "submission_worker.py",
    "juno_res_bench/__init__.py",
    "juno_res_bench/resolution.py",
    "juno_res_bench/sparse_waveforms.py",
    "calibration/index.npz",
    "calibration/labels.npz",
    "calibration/metadata.json",
    "calibration/segment_samples.npy",
    "dev/index.npz",
    "dev/metadata.json",
    "dev/segment_samples.npy",
    "dev/truth.npz",
}
PRIVATE_FILES = {
    "truth.npz",
    "final_observations/index.npz",
    "final_observations/metadata.json",
    "final_observations/segment_samples.npy",
}
PRIVATE_OPTIONAL_FILES = {"validation.json"}


def _per_event_steps(values, offsets):
    """Sum a ragged per-step array into per-event totals."""
    return np.add.reduceat(np.asarray(values), np.asarray(offsets)[:-1])


def physics_checks(truth):
    """Energy conservation, quenching depth, and annihilation topology."""
    steps = np.asarray(truth["step_e_dep_mev"])
    conservation = np.abs(
        _per_event_steps(steps, truth["step_offsets"])
        + np.asarray(truth["evt_e_escape_mev"], dtype=float)
        - np.asarray(truth["evt_total_energy"], dtype=float)
    )
    record = {
        "energy_conservation_max_error_mev": float(conservation.max()),
        "energy_conservation_pass": bool(conservation.max() < 1e-8),
    }

    kinetic = np.asarray(truth["step_kinetic_mev"], dtype=float)
    fraction = (
        np.asarray(truth["step_e_vis_mev"], dtype=float)
        / np.maximum(steps, 1e-12)
    )
    low = fraction[kinetic < 0.05]
    mid = fraction[(kinetic >= 0.5) & (kinetic <= 2.0)]
    record["mean_visible_fraction_below_50kev"] = float(low.mean())
    record["mean_visible_fraction_0p5_to_2mev"] = float(mid.mean())
    record["quenching_pass"] = bool(
        low.size and mid.size and low.mean() < mid.mean()
    )

    kind = np.asarray(truth["step_kind"])
    annihilation = np.isin(kind, ANNIHILATION_KINDS)
    positions = np.asarray(truth["step_pos_m"], dtype=float).reshape(-1, 3)
    offsets = np.asarray(truth["step_offsets"])
    annihilation_energy = []
    extents = []
    for event in range(len(offsets) - 1):
        lo, hi = int(offsets[event]), int(offsets[event + 1])
        rows = annihilation[lo:hi]
        annihilation_energy.append(steps[lo:hi][rows].sum())
        if rows.any():
            spread = positions[lo:hi][rows] - positions[lo:hi][rows].mean(axis=0)
            extents.append(float(np.linalg.norm(spread, axis=1).mean()))
    record["annihilation_mean_energy_mev"] = float(
        np.mean(annihilation_energy) if annihilation_energy else 0.0
    )
    record["annihilation_mean_extent_mm"] = (
        1000.0 * float(np.mean(extents)) if extents else 0.0
    )
    record["annihilation_pass"] = bool(
        record["annihilation_mean_extent_mm"] > 0.0
        and abs(record["annihilation_mean_energy_mev"] - ANNIHILATION_MEV)
        < 1e-6
    )
    return record


def hygiene_checks(public_package, private_root):
    """No private key or truth array appears in the public package."""
    reasons = []
    public = Path(public_package)
    private = Path(private_root)
    public_files = {
        str(path.relative_to(public)) for path in public.rglob("*") if path.is_file()
    }
    private_files = {
        str(path.relative_to(private)) for path in private.rglob("*") if path.is_file()
    }
    for extra in sorted(public_files - PUBLIC_FILES):
        reasons.append(f"public package contains unexpected file: {extra}")
    for missing in sorted(PUBLIC_FILES - public_files):
        reasons.append(f"public package is missing required file: {missing}")
    for extra in sorted(private_files - PRIVATE_FILES - PRIVATE_OPTIONAL_FILES):
        reasons.append(f"private package contains unexpected file: {extra}")
    for missing in sorted(PRIVATE_FILES - private_files):
        reasons.append(f"private package is missing required file: {missing}")
    for split in (private / "final_observations", public / "calibration",
                  public / "dev"):
        metadata = json.loads((split / "metadata.json").read_text())
        for key in FORBIDDEN_METADATA_KEYS:
            if key in metadata:
                reasons.append(f"{split.name}/metadata.json leaks {key}")
    if (private / "final_observations" / "truth.npz").exists():
        reasons.append("final observations carry truth.npz")
    for relative, allowed in PUBLIC_NPZ_KEYS.items():
        with np.load(public / relative, allow_pickle=False) as data:
            if set(data.files) != allowed:
                reasons.append(f"{relative} exposes unexpected arrays")
    return {"hygiene_pass": not reasons, "hygiene_reasons": reasons}


def bootstrap_resolution_std(probe_kinetic, probe_rec, seed, replicates):
    """Deterministic event-bootstrap spread of the scalar score.

    A replicate whose refit leaves the physical resolution-parameter space
    counts as a failed replicate: at adequate statistics this is rare, and
    a substantial failure rate is itself boundary instability.
    """
    rng = np.random.default_rng(seed)
    kinetic = np.asarray(probe_kinetic, dtype=float)
    reconstructed = np.asarray(probe_rec, dtype=float)
    groups = [reconstructed[kinetic == value] for value in PROBE_KINETIC_MEV]
    scores = []
    failed = 0
    for _ in range(int(replicates)):
        means = []
        widths = []
        try:
            for group in groups:
                resampled = group[rng.integers(0, len(group), len(group))]
                mean, width = fit_peak(resampled)
                means.append(mean)
                widths.append(width)
            scores.append(fit_resolution_curve(means, widths).r_1mev)
        except ValueError:
            failed += 1
    spread = float(np.std(np.asarray(scores), ddof=1)) if len(scores) > 1 else float("inf")
    return spread, len(scores), failed


def validate_world(
    truth,
    baseline_predictions,
    reference_predictions,
    public_package=None,
    private_root=None,
    bootstrap_seed=17,
    bootstrap_replicates=200,
):
    """Run every release gate that does not need regeneration."""
    role = np.asarray(truth["evt_sample_role"])
    probe = role == 0
    control = role == 1
    kinetic = np.asarray(truth["evt_e_true"], dtype=float)
    visible = np.asarray(truth["evt_e_vis"], dtype=float)

    failures = []
    report = {"n_probe_events": int(probe.sum()),
              "n_control_events": int(control.sum())}
    report["probe_events_per_energy"] = {
        str(float(value)): int(np.count_nonzero(probe & (kinetic == value)))
        for value in PROBE_KINETIC_MEV
    }

    report.update(physics_checks(truth))
    if not report["energy_conservation_pass"]:
        failures.append("energy_conservation_violation")
    if not report["quenching_pass"]:
        failures.append("quenching_not_energy_dependent")
    if not report["annihilation_pass"]:
        failures.append("annihilation_topology_or_energy_wrong")

    if public_package is not None and private_root is not None:
        report.update(hygiene_checks(public_package, private_root))
        if not report["hygiene_pass"]:
            failures.append("private_truth_in_public_package")

    scores = {}
    for name, predictions in (
        ("baseline", baseline_predictions),
        ("reference", reference_predictions),
    ):
        score = score_v2(kinetic[probe], np.asarray(predictions)[probe],
                         visible[control], np.asarray(predictions)[control])
        scores[name] = score
        report[f"{name}_valid"] = score["valid"]
        report[f"{name}_R_1MeV_percent"] = (
            100.0 * score["R_1MeV"] if score["valid"] else None
        )

    if not scores["reference"]["valid"] or (
        scores["reference"]["R_1MeV"] > TARGET_R_1MEV
    ):
        failures.append("reference_does_not_reach_target")
    if not scores["baseline"]["valid"]:
        failures.append("public_baseline_invalid")
    elif scores["baseline"]["R_1MeV"] <= TARGET_R_1MEV:
        failures.append("public_baseline_reaches_target")

    if scores["reference"]["valid"]:
        spread, used, failed = bootstrap_resolution_std(
            kinetic[probe],
            np.asarray(reference_predictions)[probe],
            bootstrap_seed,
            bootstrap_replicates,
        )
        report["score_bootstrap_std_percent_point"] = 100.0 * spread
        report["score_bootstrap_replicates"] = used
        report["score_bootstrap_failed_replicates"] = failed
        if 100.0 * spread > 0.03 or failed > 0.1 * int(bootstrap_replicates):
            failures.append("score_boundary_unstable")

    report["failures"] = failures
    report["release_ready"] = not failures
    return report


def _truth_bytes(truth):
    stream = b"".join(
        np.ascontiguousarray(truth[key]).tobytes()
        for key in sorted(truth)
    )
    return hashlib.sha256(stream).hexdigest()


def _observation_hash(observations):
    """Hash waveform content and sparse indexing, not private truth alone."""
    digest = hashlib.sha256()
    for event in observations:
        for value in (
            event.pmt_ids,
            event.segment_pmt_ids,
            event.segment_start_samples,
            event.segment_sample_offsets,
            event.samples,
        ):
            array = np.ascontiguousarray(value)
            digest.update(array.dtype.str.encode())
            digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
            digest.update(array.tobytes())
        digest.update(np.asarray([
            event.baseline,
            event.n_samples,
            event.threshold_adc,
            event.pre_samples,
            event.post_samples,
        ], dtype=np.int64).tobytes())
    return digest.hexdigest()


def reproducibility_check(seed, n_pmt, events_per_point=1):
    """Regenerate the same small population twice and compare hashes."""
    from benchmarks.JunoResBench.juno_res_bench.geometry import PMTLayout
    from benchmarks.JunoResBench.scripts.generate_v2_dataset import (
        make_population,
        simulate_population,
        v2_detector_config,
    )

    population = make_population("probes", seed, events_per_point=events_per_point)
    truth_hashes = []
    observation_hashes = []
    started = time.perf_counter()
    for _ in range(2):
        cfg = v2_detector_config()
        layout = PMTLayout.uniform(n_pmt, cfg.detector_radius_m)
        bundle = simulate_population(population, seed=seed + 1, layout=layout)
        truth_hashes.append(_truth_bytes(bundle["truth"]))
        observation_hashes.append(_observation_hash(bundle["observations"]))
    seconds = (time.perf_counter() - started) / (2 * len(population["evt_e_true"]))
    return {
        "generation_reproducible": (
            truth_hashes[0] == truth_hashes[1]
            and observation_hashes[0] == observation_hashes[1]
        ),
        "generation_seconds_per_event": seconds,
        "generation_truth_hash": truth_hashes[0],
        "generation_observation_hash": observation_hashes[0],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", required=True,
                        help="blind_truth_v2 root (final_observations + truth.npz)")
    parser.add_argument("--baseline", required=True,
                        help="public charge baseline submission file")
    parser.add_argument("--reference", required=True,
                        help="reviewed waveform-only reference submission file")
    parser.add_argument("--public", default=None,
                        help="task_v2 root (default: sibling of --private)")
    parser.add_argument("--out", required=True, help="validation JSON path")
    parser.add_argument("--bootstrap-seed", type=int, default=17)
    parser.add_argument("--bootstrap-replicates", type=int, default=200)
    parser.add_argument("--repro-seed", type=int, default=424242)
    args = parser.parse_args()

    private_root = Path(args.private)
    public_root = Path(args.public) if args.public else private_root.parent / "task_v2"
    with np.load(private_root / "truth.npz", allow_pickle=False) as data:
        truth = {key: data[key] for key in data.files}

    predictions = {}
    timings = {}
    started = time.perf_counter()
    evaluate_v2.run_online(
        args.baseline, public_root / "dev",
        public_root / "calibration", public_root / "detector_geometry.npz",
    )
    timings["development_baseline_evaluation_seconds"] = (
        time.perf_counter() - started
    )
    for name, path in (("baseline", args.baseline), ("reference", args.reference)):
        started = time.perf_counter()
        predictions[name] = evaluate_v2.run_online(
            path, private_root,
            public_root / "calibration", public_root / "detector_geometry.npz",
        )
        timings[f"{name}_evaluation_seconds"] = time.perf_counter() - started

    report = validate_world(
        truth, predictions["baseline"], predictions["reference"],
        public_package=public_root, private_root=private_root,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_replicates=args.bootstrap_replicates,
    )

    n_pmt = json.loads(
        (private_root / "final_observations" / "metadata.json").read_text()
    )["n_pmt"]
    report.update(reproducibility_check(args.repro_seed, n_pmt))
    if not report["generation_reproducible"]:
        report["failures"].append("generation_not_reproducible")
        report["release_ready"] = False

    observations = private_root / "final_observations"
    bytes_total = sum(
        path.stat().st_size
        for path in observations.rglob("*") if path.is_file()
    ) + (private_root / "truth.npz").stat().st_size
    report["compressed_bytes_per_event"] = bytes_total / (
        report["n_probe_events"] + report["n_control_events"]
    )
    report.update(timings)
    if timings["development_baseline_evaluation_seconds"] > 120.0:
        report["failures"].append("development_baseline_too_slow")
        report["release_ready"] = False
    report["reference_environment"] = {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
    }

    rendered = json.dumps(report, indent=2)
    Path(args.out).write_text(rendered + "\n")
    print(rendered)
    sys.exit(0 if report["release_ready"] else 1)


if __name__ == "__main__":
    main()
