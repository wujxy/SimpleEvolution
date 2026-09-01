"""Black-box contract and active-code isolation checks."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_text(directory):
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in directory.rglob("*.py")
    )


def test_contract_is_data_only_and_declares_sparse_fields():
    contract = ROOT / "contract"

    assert {path.suffix for path in contract.iterdir()} == {".json"}
    sparse = json.loads(
        (contract / "sparse_waveform_v1.json").read_text(encoding="utf-8")
    )
    assert sparse["files"]["index.npz"]["event_segment_offsets"] == "int64[N+1]"
    assert sparse["files"]["segment_samples.npy"] == "int16[M]"


def test_active_generator_and_evaluators_have_no_cross_imports():
    generator = ROOT / "world_generator"
    evaluators = ROOT / "tasks"

    generator_text = _python_text(generator)
    evaluator_text = _python_text(evaluators)

    assert "tasks.electron_single_site.evaluator" not in generator_text
    assert "tasks.ibd_positron_multisite.evaluator" not in generator_text
    assert "world_generator" not in evaluator_text
    assert "juno_res_bench" not in evaluator_text


def test_readme_names_two_tier_tasks_as_active_benchmarks():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "tasks/electron_single_site" in readme
    assert "tasks/ibd_positron_multisite" in readme
