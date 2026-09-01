"""Process-level checks for the isolated two-tier generator."""

from pathlib import Path
import os
import subprocess
import sys

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "world_generator" / "build_task.py"
pytestmark = pytest.mark.skipif(
    os.environ.get("JRB_RUN_GENERATION") != "1",
    reason="real waveform generation runs only on the designated batch cluster",
)


def _build(task_name, output_root):
    subprocess.run(
        [
            sys.executable,
            str(BUILD),
            "--task", task_name,
            "--out", str(output_root),
            "--seed", "20260901",
            "--n-pmt", "16",
            "--calibration-events-per-point", "1",
            "--probe-events-per-point", "1",
            "--controls", "64",
        ],
        check=True,
    )


def _geometry(root):
    with np.load(root / "public" / "detector_geometry.npz") as data:
        return data["pmt_positions_m"]


def _truth(root):
    with np.load(root / "private" / "truth.npz") as data:
        return {key: data[key] for key in data.files}


def test_generator_writes_only_data_into_dataset(tmp_path):
    output = tmp_path / "electron"

    _build("electron_single_site", output)

    assert (output / "public" / "calibration" / "index.npz").is_file()
    assert (output / "private" / "truth.npz").is_file()
    assert not list(output.rglob("*.py"))


def test_two_tasks_share_geometry_and_differ_in_particle_topology(tmp_path):
    electron = tmp_path / "electron"
    positron = tmp_path / "positron"

    _build("electron_single_site", electron)
    _build("ibd_positron_multisite", positron)

    assert np.array_equal(_geometry(electron), _geometry(positron))
    assert set(_truth(electron)["evt_particle_type"]) == {0}
    assert set(_truth(positron)["evt_particle_type"]) == {2}


def test_probe_role_stays_aligned_with_shuffled_probe_energy(tmp_path):
    output = tmp_path / "electron"

    _build("electron_single_site", output)

    truth = _truth(output)
    probes = np.sort(truth["evt_e_true"][truth["evt_sample_role"] == 0])
    assert np.array_equal(probes, np.arange(1.0, 11.0))
