"""Release-validator boundary checks."""

from pathlib import Path

import numpy as np

from benchmarks.JunoResBench.world_generator.validate_release import hygiene_report
from benchmarks.JunoResBench.world_generator.validate_release import physics_report


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
