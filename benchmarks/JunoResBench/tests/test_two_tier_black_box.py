"""Process-level checks for the isolated two-tier generator."""

from pathlib import Path
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.geometry import (
    PMT_GENERIC,
)
from benchmarks.JunoResBench.world_generator.build_task import select_layout


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
            "--geometry-mode", "uniform",
            "--n-pmt", "16",
            "--calibration-events-per-point", "1",
            "--probe-events-per-point", "1",
            "--controls", "64",
        ],
        check=True,
    )


def test_select_layout_uses_aligned_juno_pair(tmp_path):
    pos = tmp_path / "pos.csv"
    typ = tmp_path / "type.csv"
    pos.write_text(
        "0 0 0 19365 0 0\n1 19365 0 0 90 0\n2 0 0 -19365 180 0\n",
        encoding="utf-8",
    )
    typ.write_text(
        "2 HighQENNVT\n0 Hamamatsu\n1 NNVT\n",
        encoding="utf-8",
    )

    layout = select_layout("juno", None, pos, typ)

    assert layout.n_pmt == 3
    assert set(layout.pmt_model) == {0, 1, 2}


def test_select_layout_keeps_uniform_explicit():
    layout = select_layout("uniform", 16, None, None)

    assert layout.n_pmt == 16
    assert np.all(layout.pmt_model == PMT_GENERIC)


def test_select_layout_juno_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        select_layout("juno", None, tmp_path / "missing-pos", tmp_path / "missing-type")


def _geometry(root):
    with np.load(root / "public" / "detector_geometry.npz") as data:
        return {key: data[key] for key in data.files}


def _truth(root):
    with np.load(root / "private" / "truth.npz") as data:
        return {key: data[key] for key in data.files}


def test_generator_writes_only_data_into_dataset(tmp_path):
    output = tmp_path / "electron"

    _build("electron_single_site", output)

    assert (output / "public" / "calibration" / "index.npz").is_file()
    assert (output / "private" / "truth.npz").is_file()
    assert not list(output.rglob("*.py"))
    geometry = _geometry(output)
    assert set(geometry) == {
        "pmt_positions_m", "pmt_copy_no", "pmt_model"
    }
    assert np.array_equal(geometry["pmt_copy_no"], np.arange(16))
    assert np.all(geometry["pmt_model"] == PMT_GENERIC)
    assert not {
        "gain", "pde_delta", "time_offset_ns", "tts_sigma_ns"
    } & set(geometry)
    metadata = json.loads(
        (output / "public" / "dev" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["layout"] == "uniform"
    assert metadata["geometry_sha256"] == []
    assert metadata["pmt_model_counts"] == {"generic": 16}


def test_two_tasks_share_geometry_and_differ_in_particle_topology(tmp_path):
    electron = tmp_path / "electron"
    positron = tmp_path / "positron"

    _build("electron_single_site", electron)
    _build("ibd_positron_multisite", positron)

    assert np.array_equal(
        _geometry(electron)["pmt_positions_m"],
        _geometry(positron)["pmt_positions_m"],
    )
    assert set(_truth(electron)["evt_particle_type"]) == {0}
    assert set(_truth(positron)["evt_particle_type"]) == {2}


def test_probe_role_stays_aligned_with_shuffled_probe_energy(tmp_path):
    output = tmp_path / "electron"

    _build("electron_single_site", output)

    truth = _truth(output)
    probes = np.sort(truth["evt_e_true"][truth["evt_sample_role"] == 0])
    assert np.array_equal(probes, np.arange(1.0, 11.0))
