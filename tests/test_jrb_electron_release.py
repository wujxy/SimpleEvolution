from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from singlenode_mounts import parse_read_only_mounts


def test_parse_read_only_mounts():
    assert parse_read_only_mounts("/host/public:/data/jrb/public") == [
        ("/host/public", "/data/jrb/public")
    ]


def test_parse_read_only_mounts_rejects_malformed_value():
    with pytest.raises(ValueError, match="SOURCE:DESTINATION"):
        parse_read_only_mounts("/host/public")


def test_node_container_maps_explicit_mount_read_only():
    root = Path(__file__).resolve().parents[1]
    script = f'''\
source "{root}/singlenode/node_common.sh"
RUN_DIR=/unused/run
NODE_TEMPLATE=/unused/template
SPEC_TEMPLATE=/unused/spec.json
NODE_IMAGE=/unused/image.sif
EXTRA_RO_MOUNTS=/host/public:/data/jrb/public
apptainer() {{ printf '%s\\n' "$@"; }}
node_container /bin/true
'''
    result = subprocess.run(
        ["bash", "-c", script], check=True, capture_output=True, text=True
    )
    assert "/host/public:/data/jrb/public:ro" in result.stdout.splitlines()


def test_electron_research_world_is_public_only_and_has_no_t0():
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "junoresbench_electron_single_site_std_opt"
    launcher = (example / "launch_singlenode.sh").read_text(encoding="utf-8")
    spec = (example / "spec.json").read_text(encoding="utf-8")
    template_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (example / "repo").rglob("*")
        if path.is_file() and path.suffix in {".md", ".py", ".sh"}
    )

    assert "release/public:/data/jrb/electron_single_site_public" in launcher
    assert "release/private" not in launcher
    assert "world_generator" not in template_text
    assert "/private" not in template_text
    assert "R_1MeV <= 3.0%" in spec
    assert "E_rec, x_rec, y_rec, z_rec" in spec
    assert "t0" not in spec.lower()
    assert (example / "repo/benchmarks/electron_single_site/data").readlink() == Path(
        "/data/jrb/electron_single_site_public"
    )


def test_electron_baseline_writes_four_finite_arrays(tmp_path):
    root = Path(__file__).resolve().parents[1]
    evaluator = root / "benchmarks/JunoResBench/tasks/electron_single_site/evaluator"
    sys.path.insert(0, str(evaluator))
    from sparse_reader import SparseEvent, write_sparse_split

    positions = np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]
    )
    events = []
    for pmt_id, charge in enumerate((10, 20, 30, 40)):
        events.append(
            SparseEvent(
                pmt_ids=np.array([pmt_id], dtype=np.int32),
                segment_pmt_ids=np.array([pmt_id], dtype=np.int32),
                segment_start_samples=np.array([0], dtype=np.int16),
                segment_sample_offsets=np.array([0, 1], dtype=np.int64),
                samples=np.array([-charge], dtype=np.int16),
                baseline=100,
                n_samples=1,
                threshold_adc=1,
                pre_samples=0,
                post_samples=0,
            )
        )
    public = tmp_path / "public"
    calibration = public / "calibration"
    development = public / "dev"
    write_sparse_split(calibration, {}, events)
    write_sparse_split(development, {}, events)
    np.savez(
        calibration / "labels.npz",
        source_energy_mev=np.arange(1.0, 5.0),
        deployment_position_m=positions,
    )
    np.savez(public / "detector_geometry.npz", pmt_positions_m=positions)
    output = tmp_path / "prediction.npz"

    subprocess.run(
        [
            "python",
            str(root / "examples/junoresbench_electron_single_site_std_opt/repo/src/solve.py"),
            "--data",
            str(development),
            "--calibration",
            str(calibration),
            "--out",
            str(output),
        ],
        check=True,
    )

    with np.load(output, allow_pickle=False) as prediction:
        assert set(prediction.files) == {"E_rec", "x_rec", "y_rec", "z_rec"}
        assert all(prediction[name].shape == (4,) for name in prediction.files)
        assert all(np.isfinite(prediction[name]).all() for name in prediction.files)
        assert np.allclose(prediction["E_rec"], np.arange(1.0, 5.0))
