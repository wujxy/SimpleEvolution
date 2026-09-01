#!/usr/bin/env python3
"""Sandbox-side binary protocol for one JunoResBench submission."""

from contextlib import redirect_stdout
import importlib.util
import pickle
import struct
import sys
import traceback

sys.dont_write_bytecode = True

_INPUT = sys.stdin.buffer
_OUTPUT = sys.stdout.buffer


def _send(status, payload=b""):
    _OUTPUT.write(struct.pack("!cQ", status, len(payload)))
    _OUTPUT.write(payload)
    _OUTPUT.flush()


def _receive_event():
    header = _INPUT.read(8)
    if len(header) != 8:
        raise EOFError("truncated event header")
    size = struct.unpack("!Q", header)[0]
    if size == 0:
        return None
    payload = _INPUT.read(size)
    if len(payload) != size:
        raise EOFError("truncated event payload")
    return pickle.loads(payload)


def _load_submission():
    spec = importlib.util.spec_from_file_location("juno_v2_submission", "/submission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "Submission", None)
    if factory is None:
        raise ValueError("submission does not expose a Submission class")
    return factory()


def main():
    try:
        with redirect_stdout(sys.stderr):
            submission = _load_submission()
            submission.prepare("/task/calibration", "/task/detector_geometry.npz")
        _send(b"O")
        while True:
            event = _receive_event()
            if event is None:
                return
            with redirect_stdout(sys.stderr):
                value = float(submission.predict(event))
            _send(b"O", struct.pack("!d", value))
    except Exception:
        _send(b"E", traceback.format_exc().encode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
