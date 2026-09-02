#!/usr/bin/env python3
"""Standalone online evaluator for electron energy and vertex reconstruction."""

import argparse
import json
import os
from pathlib import Path
import pickle
import resource
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

from scoring import parse_prediction, score_electron
from sparse_reader import SparseSplit


sys.dont_write_bytecode = True
WALL_SECONDS = 3600
MEMORY_BYTES = 8 * 1024**3


def _command(submission, evaluator, public):
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("bubblewrap is required for hidden-stream evaluation")
    command = [bwrap, "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--new-session"]
    for path in (Path("/usr"), Path("/lib"), Path("/lib64"), Path(sys.prefix).resolve()):
        if path.exists():
            command.extend(("--ro-bind", str(path), str(path)))
    return command + ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", str(evaluator.resolve()), "/task", "--ro-bind", str(public.resolve()), "/data", "--ro-bind", str(Path(submission).resolve()), "/submission.py", "--chdir", "/task", sys.executable, "/task/submission_worker.py"]


def _limits():
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (WALL_SECONDS, WALL_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))


def _read(stream, size, deadline):
    chunks = []
    while size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise TimeoutError("submission exceeded the wall-time limit")
        block = os.read(stream.fileno(), size)
        if not block:
            raise EOFError("submission worker closed its protocol stream")
        chunks.append(block)
        size -= len(block)
    return b"".join(chunks)


def _message(stream, deadline):
    status, size = struct.unpack("!cQ", _read(stream, 9, deadline))
    if size > 1024 * 1024:
        raise RuntimeError("submission worker returned an oversized message")
    payload = _read(stream, size, deadline)
    if status == b"E":
        raise RuntimeError(payload.decode("utf-8", errors="replace"))
    if status != b"O":
        raise RuntimeError("submission worker returned an invalid status")
    return payload


def run_online(submission, private_root, public_root):
    """Send private observations one at a time into the isolated worker."""
    evaluator = Path(__file__).resolve().parent
    deadline = time.monotonic() + WALL_SECONDS
    stderr = tempfile.TemporaryFile(mode="w+b")
    process = subprocess.Popen(_command(submission, evaluator, Path(public_root)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env={"HOME": "/tmp", "PATH": "/usr/bin:/bin", "PYTHONPATH": "/task", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}, preexec_fn=_limits)
    try:
        _message(process.stdout, deadline)
        split = SparseSplit(Path(private_root) / "final_observations")
        output = np.empty((len(split), 4), dtype=float)
        for index, event in enumerate(split.iter_events()):
            payload = pickle.dumps(event, protocol=5)
            process.stdin.write(struct.pack("!Q", len(payload)) + payload)
            process.stdin.flush()
            response = _message(process.stdout, deadline)
            if len(response) != 32:
                raise RuntimeError("submission worker returned malformed prediction")
            output[index] = struct.unpack("!dddd", response)
        process.stdin.write(struct.pack("!Q", 0))
        process.stdin.close()
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return output
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stderr.close()


def score_predictions(truth, prediction, config):
    """Route frozen probe and continuous-control populations to scoring."""
    role = np.asarray(truth["evt_sample_role"])
    prediction = np.asarray(prediction, dtype=float)
    if prediction.shape != (len(role), 4):
        raise ValueError("prediction must have shape [event,4]")
    probe = role == 0
    control = role == 1
    if not probe.any() or not control.any() or np.any(~(probe | control)):
        raise ValueError("evt_sample_role must contain only probe=0 and control=1")
    return score_electron(
        np.asarray(truth["evt_e_true"])[probe],
        prediction[probe, 0],
        np.asarray(truth["evt_vertex_m"])[probe],
        prediction[probe, 1:],
        np.asarray(truth["evt_e_vis"])[control],
        prediction[control, 0],
        config["vertex_threshold_m"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private", required=True)
    parser.add_argument("--public", required=True)
    parser.add_argument("--submission", required=True)
    args = parser.parse_args()
    prediction = run_online(args.submission, args.private, args.public)
    with np.load(Path(args.private) / "truth.npz") as truth:
        config = json.loads((Path(args.public) / "evaluation_config.json").read_text())
        result = score_predictions(truth, prediction, config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
