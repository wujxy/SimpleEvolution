"""Config round-trip for the new frontier fields."""
from __future__ import annotations

from pathlib import Path

from simpleevo.config import EvolutionConfig, load_config

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "tiny_algo_opt"


def _minimal_raw(**overrides) -> dict:
    raw = {
        "goal": "test",
        "repo_path": "/x",
        "runtime_image": "/y",
        "eval_commands": ["echo TOTAL_MS=100"],
        "metrics_schema": {"objective": {"key": "TOTAL_MS", "lower_is_better": True}},
        "axes": ["TOTAL_MS"],
    }
    raw.update(overrides)
    return raw


def test_frontier_fields_round_trip():
    config = EvolutionConfig.from_dict(_minimal_raw(
        frontier_policy="topk",
        frontier_top_k=5,
        max_research_per_node=7,
    ))
    assert config.frontier_policy == "topk"
    assert config.frontier_top_k == 5
    assert config.max_research_per_node == 7

    loaded = EvolutionConfig.from_dict(config.to_dict())
    assert loaded.frontier_policy == "topk"
    assert loaded.frontier_top_k == 5
    assert loaded.max_research_per_node == 7


def test_frontier_fields_defaults():
    config = EvolutionConfig.from_dict(_minimal_raw())
    assert config.frontier_policy == "gepa"
    assert config.frontier_top_k == 3
    assert config.max_research_per_node == 3


def test_example_config_new_fields():
    config = load_config(_EXAMPLE_DIR / "task.yaml")
    assert config.frontier_policy == "gepa"
    assert config.frontier_top_k == 3
    assert config.max_research_per_node == 3
