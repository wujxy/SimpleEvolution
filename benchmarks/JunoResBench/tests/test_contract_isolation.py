"""Black-box contract and active-code isolation checks."""

import json
from pathlib import Path
import subprocess


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


def test_legacy_junoresbench_assets_are_not_tracked():
    project = ROOT.parents[1]
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=project, text=True
    ).splitlines()
    legacy_prefixes = (
        "benchmarks/JunoResBench/data/",
        "benchmarks/JunoResBench/blind_task_",
        "benchmarks/JunoResBench/blind_truth_",
        "benchmarks/JunoResBench/whitebox_task_",
        "benchmarks/JunoResBench/juno_res_bench/",
        "benchmarks/JunoResBench/task_v2/",
        "examples/junoresbench_full_opt/",
        "examples/junoresbench_full_std_opt/",
        "examples/junoresbench_static_opt/",
        "examples/junoresbench_wb_opt/",
        "singlenode/specs/jrb_full_",
        "singlenode/specs/jrb_static_",
        "singlenode/specs/jrb_wb_",
        "scripts/replay_jrb_wb.py",
        "scripts/fig_jrb_wb_final.py",
    )
    leaked = [path for path in tracked if path.startswith(legacy_prefixes)]
    assert not leaked, "legacy JunoResBench assets remain tracked: " + str(leaked[:5])
