#!/usr/bin/env python3
"""Validate serialized release artifacts and build the mandatory visual atlas."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from benchmarks.JunoResBench.scripts.plot_electron_single_site_waveforms import (
    FIGURE_NAMES,
    build_waveform_figures,
)


DEFERRED_GATES = [
    "baseline_above_target",
    "expert_reference_reaches_target",
    "score_bootstrap_stability",
]

FIGURE_PURPOSE = {
    "vertex_distribution": "顶点总体是否符合球体部署",
    "energy_radius_coverage": "能量与位置覆盖是否完整",
    "radial_light_yield": "光收集位置依赖是否可见",
    "hit_pattern_comparison": "中心/边缘 hit pattern 是否不同",
    "charge_pattern_comparison": "电荷空间梯度是否编码位置",
    "hit_multiplicity_vs_energy": "占用数是否随能量增长",
    "charge_vs_energy": "积分电荷是否保存能量信息",
    "event_anatomy": "单事件空间、时间和波形是否自洽",
    "first_hit_time": "prompt 与晚光结构是否存在",
    "time_vs_distance": "首光是否随传播距离推迟",
    "tof_corrected_residual": "TOF 校正后是否有 prompt core 和晚尾",
    "timing_vs_radius": "trigger-relative timing 是否有位置依赖",
    "waveform_examples": "低/中/高电荷波形是否合理",
    "waveform_overlays": "脉冲成形模板是否稳定",
    "pulse_integral_vs_peak": "峰高与积分是否自洽",
    "roi_structure": "稀疏 ROI 是否真正稀疏",
}

FIGURE_GATE = {
    "charge_vs_energy": ("charge_energy_correlation",),
    "time_vs_distance": ("time_distance_slope_ns_per_m",),
    "roi_structure": (
        "roi_start_zero_fraction",
        "roi_near_full_window_fraction",
        "sparse_to_stored_dense_ratio",
    ),
}


def hygiene_report(public_root, private_root):
    """Reject executable payloads from generated public/private data trees."""
    forbidden = (".py", ".pyc", ".so", ".sh")
    unexpected = []
    for label, root in (("public", Path(public_root)), ("private", Path(private_root))):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in forbidden:
                unexpected.append(f"{label}/{path.relative_to(root)}")
    return {"pass": not unexpected, "unexpected_executables": sorted(unexpected)}


def physics_report(task_name, truth_path):
    """Check private particle truth without importing any evaluator."""
    with np.load(truth_path, allow_pickle=False) as data:
        truth = {key: data[key] for key in data.files}
    report = {}
    if {
        "step_offsets", "step_e_dep_mev", "evt_e_escape_mev", "evt_total_energy"
    } <= set(truth):
        deposited = np.add.reduceat(
            truth["step_e_dep_mev"], truth["step_offsets"][:-1]
        )
        error = np.abs(
            deposited + truth["evt_e_escape_mev"] - truth["evt_total_energy"]
        )
        report["energy_conservation_max_error_mev"] = float(error.max())
        report["energy_conservation_pass"] = bool(error.max() < 1e-8)
    if {"step_e_dep_mev", "step_e_vis_mev", "step_kinetic_mev"} <= set(truth):
        fraction = truth["step_e_vis_mev"] / np.maximum(
            truth["step_e_dep_mev"], 1e-12
        )
        kinetic = truth["step_kinetic_mev"]
        low = fraction[kinetic < 0.05]
        mid = fraction[(kinetic >= 0.5) & (kinetic <= 2.0)]
        report["low_energy_visible_fraction"] = float(low.mean()) if len(low) else None
        report["mid_energy_visible_fraction"] = float(mid.mean()) if len(mid) else None
        report["quenching_pass"] = bool(
            len(low) and len(mid) and low.mean() < mid.mean()
        )
    if task_name == "ibd_positron_multisite":
        kind = truth["step_kind"]
        steps = truth["step_e_dep_mev"]
        offsets = truth["step_offsets"]
        annihilation = []
        for low_index, high_index in zip(offsets[:-1], offsets[1:]):
            selected = np.isin(kind[low_index:high_index], (3, 4, 5))
            annihilation.append(float(steps[low_index:high_index][selected].sum()))
        report["annihilation_mean_energy_mev"] = float(np.mean(annihilation))
        report["annihilation_pass"] = bool(
            abs(report["annihilation_mean_energy_mev"] - 1.021998) < 1e-6
        )
    return report


def sparse_structure_report(path):
    """Validate sparse-array boundaries without opening waveform payloads fully."""
    path = Path(path)
    required = ("metadata.json", "index.npz", "segment_samples.npy")
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        return {"pass": False, "missing": missing}
    try:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        with np.load(path / "index.npz", allow_pickle=False) as index:
            events = index["event_segment_offsets"]
            samples = index["segment_sample_offsets"]
            ids = index["segment_pmt_ids"]
            starts = index["segment_start_samples"]
        payload = np.load(path / "segment_samples.npy", mmap_mode="r", allow_pickle=False)
        valid = bool(
            events.ndim == 1
            and len(events) >= 2
            and int(events[0]) == 0
            and int(events[-1]) == len(ids)
            and samples.shape == (len(ids) + 1,)
            and int(samples[0]) == 0
            and int(samples[-1]) == len(payload)
            and starts.shape == ids.shape
            and int(metadata["n_events"]) == len(events) - 1
        )
        return {
            "pass": valid,
            "events": int(len(events) - 1),
            "segments": int(len(ids)),
            "samples": int(len(payload)),
        }
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as error:
        return {"pass": False, "error": str(error)}


def waveform_gates(summary):
    """Apply broad physical/data-volume envelopes to measured waveform metrics."""
    definitions = {
        "roi_start_zero_fraction": (
            summary["raw_roi_start_zero_fraction"], "<", 0.20,
            "window-start ROIs must not be noise-dominated",
        ),
        "roi_near_full_window_fraction": (
            summary["raw_roi_near_full_window_fraction"], "<", 0.05,
            "ROI padding must not merge most channels into full windows",
        ),
        "sparse_to_stored_dense_ratio": (
            summary["sparse_to_stored_dense_ratio"], "<", 0.35,
            "sparse storage must materially reduce stored-channel samples",
        ),
        "charge_energy_correlation": (
            summary["charge_energy_correlation"], ">", 0.0,
            "waveform charge must retain positive energy information",
        ),
        "time_distance_slope_ns_per_m": (
            summary["time_distance_slope_ns_per_m"], ">", 0.0,
            "within-event first-light time must increase with PMT distance",
        ),
    }
    gates = {}
    for name, (value, operator, limit, rationale) in definitions.items():
        finite = bool(np.isfinite(value))
        passed = finite and (value < limit if operator == "<" else value > limit)
        gates[name] = {
            "value": float(value), "operator": operator, "limit": float(limit),
            "rationale": rationale, "pass": bool(passed),
        }
    return gates


def _write_atlas(path, report):
    state = "ACCEPTED" if report["release_ready"] else "REJECTED"
    gates = report["waveform_gates"]
    lines = [
        "# JunoResBench release validation atlas", "",
        f"Overall state: **{state}**", "",
        "Expert reconstruction gates are deferred; this report validates serialized physics and observables.",
        "REVIEW means that the owner must inspect the figure before publication; no machine gate replaces that review.",
        "", "| Figure | Check | Status |", "|---|---|---|",
    ]
    for name in FIGURE_NAMES:
        gate_names = FIGURE_GATE.get(name)
        status = (
            "REVIEW" if gate_names is None
            else "PASS" if all(gates[key]["pass"] for key in gate_names)
            else "FAIL"
        )
        lines.append(f"| `{name}` | {FIGURE_PURPOSE[name]} | {status} |")
    lines.extend(("", "## Machine gates", ""))
    for name, gate in gates.items():
        status = "PASS" if gate["pass"] else "FAIL"
        lines.append(
            f"- **{status}** `{name}`: {gate['value']:.6g} "
            f"{gate['operator']} {gate['limit']:.6g} — {gate['rationale']}"
        )
    lines.extend(("", "## Figures", ""))
    for name in FIGURE_NAMES:
        lines.extend((
            f"### {name}", "", FIGURE_PURPOSE[name], "",
            f"![{name}](figures/{name}.png)", "",
        ))
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_release(task_name, release_root, output_root, sample_limit=32):
    """Build an owner-side validation bundle and return its report."""
    release = Path(release_root)
    output = Path(output_root)
    figures = output / "figures"
    output.mkdir(parents=True, exist_ok=True)
    for marker in (output / "ACCEPTED", output / "REJECTED"):
        marker.unlink(missing_ok=True)

    failures = []
    hygiene = hygiene_report(release / "public", release / "private")
    if not hygiene["pass"]:
        failures.append("dataset_contains_executable")
    structures = {
        "public_dev": sparse_structure_report(release / "public/dev"),
        "private_final": sparse_structure_report(release / "private/final_observations"),
    }
    failures.extend(
        f"invalid_structure_{name}"
        for name, result in structures.items() if not result["pass"]
    )
    physics = physics_report(task_name, release / "private/truth.npz")
    required_physics = ["energy_conservation_pass", "quenching_pass"]
    if task_name == "ibd_positron_multisite":
        required_physics.append("annihilation_pass")
    for name in required_physics:
        if not physics.get(name, False):
            failures.append(name)

    build_waveform_figures(release, figures, sample_limit=sample_limit)
    summary = json.loads((figures / "summary.json").read_text(encoding="utf-8"))
    gates = waveform_gates(summary)
    failures.extend(name for name, gate in gates.items() if not gate["pass"])
    missing_figures = [
        name for name in FIGURE_NAMES if not (figures / f"{name}.png").is_file()
    ]
    if missing_figures:
        failures.append("visual_atlas_incomplete")

    report = {
        "task": task_name, "release_ready": not failures, "failures": failures,
        "deferred": DEFERRED_GATES, "hygiene": hygiene, "structures": structures,
        "physics": physics, "waveform_summary": summary,
        "waveform_gates": gates, "missing_figures": missing_figures,
    }
    (output / "validation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_atlas(output / "README.md", report)
    marker = output / ("ACCEPTED" if report["release_ready"] else "REJECTED")
    marker.write_text(json.dumps({"task": task_name, "failures": failures}) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("electron_single_site", "ibd_positron_multisite"), required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--sample-limit", type=int, default=32)
    args = parser.parse_args()
    output = args.output or args.release / "validation"
    try:
        report = validate_release(args.task, args.release, output, args.sample_limit)
    except Exception as error:
        output.mkdir(parents=True, exist_ok=True)
        report = {
            "task": args.task, "release_ready": False,
            "failures": ["validation_exception"], "error": str(error),
            "deferred": DEFERRED_GATES,
        }
        (output / "validation_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (output / "REJECTED").write_text(str(error) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["release_ready"] else 1)


if __name__ == "__main__":
    main()
