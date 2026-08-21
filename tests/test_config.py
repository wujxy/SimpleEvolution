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
        generator_reseed=True,
    ))
    assert config.frontier_policy == "topk"
    assert config.frontier_top_k == 5
    assert config.max_research_per_node == 7
    assert config.generator_reseed is True

    loaded = EvolutionConfig.from_dict(config.to_dict())
    assert loaded.frontier_policy == "topk"
    assert loaded.frontier_top_k == 5
    assert loaded.max_research_per_node == 7
    assert loaded.generator_reseed is True


def test_frontier_fields_defaults():
    config = EvolutionConfig.from_dict(_minimal_raw())
    assert config.frontier_policy == "gepa"
    assert config.frontier_top_k == 3
    assert config.max_research_per_node == 3
    assert config.generator_reseed is False


def test_example_config_new_fields():
    config = load_config(_EXAMPLE_DIR / "task.yaml")
    assert config.frontier_policy == "topk"
    assert config.frontier_top_k == 3
    assert config.max_research_per_node == 3
    assert config.generator_reseed is True


def test_jobs_config_round_trip():
    raw = _minimal_raw(jobs={
        "backend": "condor",
        "collector": "cm01.ihep.ac.cn",
        "schedd_name": "scheduler@schedd12.ihep.ac.cn",
        "accounting_group": "JUNO.juno.default",
        "accounting_group_user": "lidian",
        "cpu_model": "zen5",
        "machine_constraint": 'Machine == "lhws316.ihep.ac.cn"',
        "memory_mb": 8192,
        "cpus": 2,
        "python_executable": "/opt/python",
    })
    config = EvolutionConfig.from_dict(raw)
    assert config.jobs.backend == "condor"
    assert config.jobs.collector == "cm01.ihep.ac.cn"
    assert config.jobs.memory_mb == 8192
    assert config.jobs.cpus == 2

    loaded = EvolutionConfig.from_dict(config.to_dict())
    assert loaded.jobs == config.jobs


def test_jobs_config_defaults_to_local():
    config = EvolutionConfig.from_dict(_minimal_raw())
    assert config.jobs.backend == "local"
    assert config.jobs.accounting_group == "JUNO.juno.default"


def test_jobs_proxy_fields_round_trip():
    raw = _minimal_raw(jobs={
        "backend": "condor",
        "http_proxy": "http://192.168.237.165:3128",
        "https_proxy": "http://192.168.237.165:3128",
        "no_proxy": "localhost,127.0.0.1,.ihep.ac.cn",
    })
    config = EvolutionConfig.from_dict(raw)
    assert config.jobs.http_proxy == "http://192.168.237.165:3128"
    assert config.jobs.https_proxy == "http://192.168.237.165:3128"
    assert config.jobs.no_proxy == "localhost,127.0.0.1,.ihep.ac.cn"

    loaded = EvolutionConfig.from_dict(config.to_dict())
    assert loaded.jobs == config.jobs


def test_job_proxy_env_emits_upper_and_lower_forms():
    job = EvolutionConfig.from_dict(_minimal_raw(jobs={
        "http_proxy": "http://192.168.237.165:3128",
        "https_proxy": "http://192.168.237.165:3128",
    })).jobs
    env = job.proxy_env()
    assert env["HTTP_PROXY"] == "http://192.168.237.165:3128"
    assert env["http_proxy"] == "http://192.168.237.165:3128"
    assert env["HTTPS_PROXY"] == "http://192.168.237.165:3128"
    assert env["https_proxy"] == "http://192.168.237.165:3128"
    # no_proxy defaults to localhost-only when a proxy is configured.
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["no_proxy"] == "localhost,127.0.0.1"


def test_job_proxy_env_honours_explicit_no_proxy():
    job = EvolutionConfig.from_dict(_minimal_raw(jobs={
        "https_proxy": "http://192.168.237.165:3128",
        "no_proxy": "localhost,127.0.0.1,.ihep.ac.cn",
    })).jobs
    env = job.proxy_env()
    assert env["NO_PROXY"] == "localhost,127.0.0.1,.ihep.ac.cn"


def test_job_proxy_env_empty_when_unconfigured():
    job = EvolutionConfig.from_dict(_minimal_raw()).jobs
    assert job.proxy_env() == {}
