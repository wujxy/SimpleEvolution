#!/usr/bin/env python3
"""Evaluate one energy-only JunoResBench v2 submission online.

Runs the submission's ``prepare`` once, then its ``predict`` once per
streamed event; the evaluator owns the prediction buffer and scores only
after the stream ends. Works both from the repository (scripts/ layout)
and inside a generated public package (task_v2/ layout with the scoring
modules copied alongside).
"""

import argparse
import json
import math
import os
from pathlib import Path
import pickle
import resource
import select
import signal
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.dont_write_bytecode = True

_HERE = Path(__file__).resolve().parent
for _base in (_HERE, _HERE.parent):
    if (_base / "juno_res_bench" / "resolution.py").is_file():
        if str(_base) not in sys.path:
            sys.path.insert(0, str(_base))
        break

from juno_res_bench.resolution import TARGET_R_1MEV, score_v2
from juno_res_bench.sparse_waveforms import SparseSplit


TRUTH_KEYS = {"evt_sample_role", "evt_e_true", "evt_e_vis"}
DEFAULT_WALL_SECONDS = 3600
DEFAULT_MEMORY_BYTES = 8 * 1024**3
MAX_STDERR_BYTES = 64 * 1024
MAX_WORKER_MESSAGE_BYTES = 1024 * 1024


def observations_dir(data_dir):
    """A private root stores its stream in final_observations/; dev does not."""
    data_dir = Path(data_dir)
    final = data_dir / "final_observations"
    return final if final.is_dir() else data_dir


def resolve_public_defaults(data_dir, calibration, geometry):
    """Default calibration and geometry to the package's public task_v2."""
    data_dir = Path(data_dir).resolve()
    candidates = (data_dir.parent / "task_v2", data_dir.parent)
    if calibration is None:
        calibration = next(
            (base / "calibration" for base in candidates
             if (base / "calibration").is_dir()),
            None,
        )
    if geometry is None:
        geometry = next(
            (base / "detector_geometry.npz" for base in candidates
             if (base / "detector_geometry.npz").is_file()),
            None,
        )
    if calibration is None or geometry is None:
        raise ValueError("no public calibration/geometry found next to --data")
    return Path(calibration), Path(geometry)


def _sandbox_command(submission_path, task_root):
    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        raise RuntimeError("bubblewrap is required for hidden-stream evaluation")
    command = [
        bubblewrap,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
    ]
    python_prefix = Path(sys.prefix).resolve()
    for host_path in (Path("/usr"), Path("/lib"), Path("/lib64"), python_prefix):
        if Path(host_path).exists():
            command.extend(("--ro-bind", str(host_path), str(host_path)))
    command.extend((
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--ro-bind", str(Path(task_root).resolve()), "/task",
        "--ro-bind", str(Path(submission_path).resolve()), "/submission.py",
        "--chdir", "/task",
        sys.executable,
        "/task/submission_worker.py",
    ))
    return command


def _read_exact(stream, size, deadline):
    blocks = []
    remaining = size
    while remaining:
        seconds = deadline - time.monotonic()
        if seconds <= 0 or not select.select([stream], [], [], seconds)[0]:
            raise TimeoutError("submission exceeded the wall-time limit")
        block = os.read(stream.fileno(), remaining)
        if not block:
            raise EOFError("submission worker closed its protocol stream")
        blocks.append(block)
        remaining -= len(block)
    return b"".join(blocks)


def _receive_worker_message(stream, deadline):
    status, size = struct.unpack("!cQ", _read_exact(stream, 9, deadline))
    if size > MAX_WORKER_MESSAGE_BYTES:
        raise RuntimeError("submission worker returned an oversized message")
    payload = _read_exact(stream, size, deadline)
    if status == b"E":
        raise RuntimeError(payload.decode("utf-8", errors="replace"))
    if status != b"O":
        raise RuntimeError("submission worker returned an invalid status")
    return payload


def _worker_limits(memory_bytes):
    """Create a process group and apply evaluator-owned hard resource limits."""
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_WALL_SECONDS, DEFAULT_WALL_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def _stop_worker(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_online(
    submission_path,
    data_dir,
    calibration_path,
    geometry_path,
    *,
    wall_time_seconds=DEFAULT_WALL_SECONDS,
    memory_bytes=DEFAULT_MEMORY_BYTES,
):
    """Stream events into a mount-isolated submission worker."""
    calibration_path = Path(calibration_path).resolve()
    geometry_path = Path(geometry_path).resolve()
    task_root = calibration_path.parent
    if geometry_path.parent != task_root:
        raise ValueError("calibration and geometry must share one public task root")
    if wall_time_seconds <= 0 or memory_bytes <= 0:
        raise ValueError("resource limits must be positive")

    stderr_file = tempfile.TemporaryFile(mode="w+b")
    deadline = time.monotonic() + float(wall_time_seconds)
    process = subprocess.Popen(
        _sandbox_command(submission_path, task_root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        env={
            "HOME": "/tmp",
            "PATH": "/usr/bin:/bin",
            "LD_LIBRARY_PATH": str(Path(sys.executable).resolve().parents[1] / "lib"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "/task",
        },
        preexec_fn=lambda: _worker_limits(int(memory_bytes)),
    )
    try:
        _receive_worker_message(process.stdout, deadline)
        split = SparseSplit(observations_dir(data_dir))
        predictions = np.zeros(len(split), dtype=float)
        for index, event in enumerate(split.iter_events()):
            payload = pickle.dumps(event, protocol=5)
            process.stdin.write(struct.pack("!Q", len(payload)))
            process.stdin.write(payload)
            process.stdin.flush()
            response = _receive_worker_message(process.stdout, deadline)
            if len(response) != 8:
                raise RuntimeError("submission worker returned a malformed prediction")
            prediction = struct.unpack("!d", response)[0]
            if not math.isfinite(prediction):
                raise RuntimeError("submission returned a non-finite prediction")
            predictions[index] = prediction
        process.stdin.write(struct.pack("!Q", 0))
        process.stdin.flush()
        process.stdin.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("submission exceeded the wall-time limit")
        return_code = process.wait(timeout=remaining)
        if return_code != 0:
            raise RuntimeError(f"submission worker exited with status {return_code}")
        return predictions
    except Exception as error:
        _stop_worker(process)
        stderr_file.seek(0)
        diagnostics = stderr_file.read(MAX_STDERR_BYTES).decode(
            "utf-8", errors="replace"
        )
        if diagnostics:
            raise RuntimeError(f"{error}\nsubmission stderr:\n{diagnostics}") from error
        raise
    finally:
        stderr_file.close()


def load_truth_partition(truth_path, predictions):
    """Partition private truth and streamed predictions by sample role."""
    with np.load(truth_path, allow_pickle=False) as truth:
        missing = TRUTH_KEYS - set(truth.files)
        if missing:
            raise ValueError(f"private truth is missing keys: {sorted(missing)}")
        role = np.asarray(truth["evt_sample_role"])
        kinetic = np.asarray(truth["evt_e_true"], dtype=float)
        visible = np.asarray(truth["evt_e_vis"], dtype=float)

    reconstructed = np.asarray(predictions, dtype=float)
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
        description="Score an energy-only JunoResBench v2 submission online"
    )
    parser.add_argument(
        "--data", required=True,
        help="private root (final_observations/ + truth.npz) or a dev split "
             "directory with truth.npz",
    )
    parser.add_argument(
        "--submission", required=True,
        help="submission file exposing a Submission class",
    )
    parser.add_argument(
        "--calibration", help="default: task_v2/calibration next to --data"
    )
    parser.add_argument(
        "--geometry", help="default: task_v2/detector_geometry.npz next to --data"
    )
    parser.add_argument("--out", help="optional score JSON output path")
    args = parser.parse_args()

    data_dir = Path(args.data)
    truth_path = data_dir / "truth.npz"
    calibration, geometry = resolve_public_defaults(
        data_dir, args.calibration, args.geometry
    )
    exit_code = 0
    try:
        predictions = run_online(args.submission, data_dir, calibration, geometry)
        result = json_result(
            score_v2(*load_truth_partition(truth_path, predictions))
        )
    except Exception as error:
        result = {
            "valid": False,
            "passed": False,
            "target_percent": 100.0 * TARGET_R_1MEV,
            "invalid_reasons": [f"submission evaluation failed: {error}"],
        }
        exit_code = 2
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.out:
        Path(args.out).write_text(rendered + "\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
