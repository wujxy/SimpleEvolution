"""Sandbox-side one-event binary protocol for electron submissions."""

from contextlib import redirect_stdout
import importlib.util
import pickle
import struct
import sys
import traceback

from scoring import parse_prediction


sys.dont_write_bytecode = True
INPUT = sys.stdin.buffer
OUTPUT = sys.stdout.buffer


def _send(status, payload=b""):
    OUTPUT.write(struct.pack("!cQ", status, len(payload)))
    OUTPUT.write(payload)
    OUTPUT.flush()


def _event():
    header = INPUT.read(8)
    if len(header) != 8:
        raise EOFError("truncated event header")
    size = struct.unpack("!Q", header)[0]
    if size == 0:
        return None
    payload = INPUT.read(size)
    if len(payload) != size:
        raise EOFError("truncated event payload")
    return pickle.loads(payload)


def main():
    try:
        spec = importlib.util.spec_from_file_location("submission", "/submission.py")
        module = importlib.util.module_from_spec(spec)
        with redirect_stdout(sys.stderr):
            spec.loader.exec_module(module)
            submission = module.Submission()
            submission.prepare("/data/calibration", "/data/detector_geometry.npz")
        _send(b"O")
        while True:
            event = _event()
            if event is None:
                return
            with redirect_stdout(sys.stderr):
                prediction = parse_prediction(submission.predict(event))
            _send(b"O", struct.pack("!dddd", *prediction))
    except Exception:
        _send(b"E", traceback.format_exc().encode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
