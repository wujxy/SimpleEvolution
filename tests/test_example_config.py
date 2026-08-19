"""Validate the shipped tiny_algo_opt example against the evaluator contract.

This locks the example's metric contract (objective float + gate booleans) to
the real ``experiment.evaluator`` parser without needing Apptainer or a model:
the toy repo is pure Python, so the eval commands run on the host interpreter.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from experiment.evaluator import _parse_metrics
from simpleevo.config import load_config

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "tiny_algo_opt"


def _run_eval_commands(repo: Path) -> str:
    """Run the three eval commands and return their combined output."""
    parts: list[str] = []
    for command in [
        "PYTHONPATH=. python -m pytest tests/ -q && echo CORRECTNESS=PASS || echo CORRECTNESS=FAIL",
        "PYTHONPATH=. python scripts/check_drift.py && echo DRIFT=PASS || echo DRIFT=FAIL",
        "PYTHONPATH=. python scripts/bench.py",
    ]:
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        parts.append(f"{completed.stdout}\n{completed.stderr}")
    return "\n".join(parts)


def test_example_config_resolves_and_declares_metrics():
    config = load_config(EXAMPLE_DIR / "task.yaml")
    assert config.repo_path.is_absolute()
    assert config.repo_path.name == "repo"
    assert config.runtime_image.is_absolute()
    assert config.axes == ("ms_per_call",)
    assert config.metrics_schema["objective"]["key"] == "ms_per_call"
    assert config.metrics_schema["objective"]["lower_is_better"] is True
    gate_keys = [g["key"] for g in config.metrics_schema["gates"]]
    assert gate_keys == ["CORRECTNESS", "DRIFT"]
    assert config.editable_paths == ("tinyalgo",)


def test_example_eval_commands_emit_parsable_metrics():
    config = load_config(EXAMPLE_DIR / "task.yaml")
    text = _run_eval_commands(EXAMPLE_DIR / "repo")
    metrics = _parse_metrics(text, dict(config.metrics_schema))
    assert isinstance(metrics["ms_per_call"], float)
    assert metrics["ms_per_call"] > 0
    assert metrics["CORRECTNESS"] is True
    assert metrics["DRIFT"] is True
