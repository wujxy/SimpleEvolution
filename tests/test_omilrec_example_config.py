"""Validate the shipped omilrec_opt example against the evaluator contract.

The OMILREC eval is heavy and needs the host JUNO stack (/cvmfs) + bench data
(/data/juno/dingxf), so unlike tiny_algo_opt we cannot run the real eval here.
Instead we lock the contract: the task.yaml resolves correctly, declares the
objective + gates, requests the read-only binds, and a representative eval
output block parses through the real ``experiment.evaluator`` parser.
"""
from __future__ import annotations

from pathlib import Path

from experiment.evaluator import _parse_metrics
from simpleevo.config import load_config

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "omilrec_opt"

# A representative tail of `bash scripts/sl_eval_v100.sh --evtmax 100` output.
_SAMPLE_EVAL_OUTPUT = """\
...
[sl_eval_v100] benchmarking probe-free production (--evtmax 100)...
OMILRECV2 ms/evt:  912.34567

CONTRACT=PASS
FCN=PASS
CONSISTENCY=PASS
SPEED_MS=912.34567  ms/evt (100 events)
SPEEDUP_V100=1.00830
SINGLE_THREADED=PASS
EVAL_RESULT=ok
"""


def test_example_config_resolves_and_declares_metrics():
    config = load_config(EXAMPLE_DIR / "task.yaml")
    assert config.repo_path.is_absolute()
    assert config.repo_path.name == "repo"
    assert config.runtime_image.is_absolute()
    assert config.axes == ("SPEED_MS",)
    assert config.metrics_schema["objective"]["key"] == "SPEED_MS"
    assert config.metrics_schema["objective"]["lower_is_better"] is True
    gate_keys = [g["key"] for g in config.metrics_schema["gates"]]
    assert gate_keys == ["CONTRACT", "FCN", "CONSISTENCY", "SINGLE_THREADED"]
    assert config.editable_paths == ("OMILRECV2/src",)
    # The eval needs the JUNO toolchain + bench data inside the sandbox.
    assert "/cvmfs" in config.read_only_binds
    assert any(p.startswith("/data/") for p in config.read_only_binds)
    assert config.eval_commands and "sl_eval_v100.sh" in config.eval_commands[0]


def test_example_eval_output_parses_metrics():
    config = load_config(EXAMPLE_DIR / "task.yaml")
    metrics = _parse_metrics(_SAMPLE_EVAL_OUTPUT, dict(config.metrics_schema))
    assert isinstance(metrics["SPEED_MS"], float)
    assert metrics["SPEED_MS"] > 0
    for gate in ("CONTRACT", "FCN", "CONSISTENCY", "SINGLE_THREADED"):
        assert metrics[gate] is True


def test_example_eval_failure_parses_gates_false():
    """A failed run (nonzero exit + FAIL tokens) must parse as failing gates."""
    config = load_config(EXAMPLE_DIR / "task.yaml")
    failing = _SAMPLE_EVAL_OUTPUT.replace("FCN=PASS", "FCN=FAIL")
    metrics = _parse_metrics(failing, dict(config.metrics_schema))
    assert metrics["FCN"] is False
    assert metrics["CONSISTENCY"] is True
