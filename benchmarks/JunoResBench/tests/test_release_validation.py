"""Release-validator boundary checks."""

import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from benchmarks.JunoResBench.world_generator.validate_release import hygiene_report
from benchmarks.JunoResBench.world_generator.validate_release import physics_report
from benchmarks.JunoResBench.world_generator.validate_release import validate_release
from benchmarks.JunoResBench.tests.test_electron_waveform_figures import (
    EXPECTED,
    _synthetic_release,
)


def test_hygiene_report_rejects_executable_in_dataset(tmp_path):
    public = tmp_path / "public"
    private = tmp_path / "private"
    public.mkdir()
    private.mkdir()
    (public / "metadata.json").write_text("{}", encoding="utf-8")
    (private / "truth.npz").write_bytes(b"fixture")
    (public / "leak.py").write_text("secret = 1", encoding="utf-8")

    report = hygiene_report(public, private)

    assert report["pass"] is False
    assert "public/leak.py" in report["unexpected_executables"]


def test_physics_report_requires_low_energy_quenching(tmp_path):
    truth = tmp_path / "truth.npz"
    np.savez_compressed(
        truth,
        step_offsets=np.array([0, 2]),
        step_e_dep_mev=np.array([0.1, 1.0]),
        step_e_vis_mev=np.array([0.08, 0.98]),
        step_kinetic_mev=np.array([0.02, 1.0]),
        evt_e_escape_mev=np.array([0.0]),
        evt_total_energy=np.array([1.1]),
    )

    report = physics_report("electron_single_site", truth)

    assert report["energy_conservation_pass"] is True
    assert report["quenching_pass"] is True


def _candidate(tmp_path, bad_roi=False):
    release = tmp_path / "release"
    _synthetic_release(release, bad_roi=bad_roi)
    (release / "private/final_observations").symlink_to(
        release / "public/dev", target_is_directory=True
    )
    return release


def test_validation_bundle_accepts_physical_candidate_without_expert(tmp_path):
    release = _candidate(tmp_path)
    output = tmp_path / "validation"

    report = validate_release(
        "electron_single_site", release, output, sample_limit=8
    )

    assert report["release_ready"] is True
    assert report["deferred"] == [
        "baseline_above_target",
        "expert_reference_reaches_target",
        "score_bootstrap_stability",
    ]
    assert (output / "ACCEPTED").is_file()
    assert not (output / "REJECTED").exists()
    assert (output / "validation_report.json").is_file()
    atlas = (output / "README.md").read_text(encoding="utf-8")
    assert all(f"figures/{name}.png" in atlas for name in EXPECTED)
    assert "| `vertex_distribution` | 顶点总体是否符合球体部署 | REVIEW |" in atlas
    assert "| `charge_vs_energy` | 积分电荷是否保存能量信息 | PASS |" in atlas


def test_validation_bundle_rejects_noise_merged_full_window_rois(tmp_path):
    release = _candidate(tmp_path, bad_roi=True)
    output = tmp_path / "validation"

    report = validate_release(
        "electron_single_site", release, output, sample_limit=8
    )

    assert report["release_ready"] is False
    assert {
        "roi_start_zero_fraction",
        "roi_near_full_window_fraction",
        "sparse_to_stored_dense_ratio",
    } <= set(report["failures"])
    assert (output / "REJECTED").is_file()
    assert not (output / "ACCEPTED").exists()
    atlas = (output / "README.md").read_text(encoding="utf-8")
    assert "| `roi_structure` | 稀疏 ROI 是否真正稀疏 | FAIL |" in atlas


def test_validator_cli_resolves_repo_imports_outside_checkout(tmp_path):
    script = Path(
        "benchmarks/JunoResBench/world_generator/validate_release.py"
    ).resolve()

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_validator_cli_writes_rejected_report_on_validation_exception(tmp_path):
    script = Path(
        "benchmarks/JunoResBench/world_generator/validate_release.py"
    ).resolve()
    release = tmp_path / "missing-release"
    output = tmp_path / "validation"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--task",
            "electron_single_site",
            "--release",
            str(release),
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert (output / "REJECTED").is_file()
    report = json.loads(
        (output / "validation_report.json").read_text(encoding="utf-8")
    )
    assert report["failures"] == ["validation_exception"]
