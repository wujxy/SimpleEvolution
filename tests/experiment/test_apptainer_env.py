"""Sandbox environment assembly: credential regime follows the config.

The executor's claude CLI prefers ANTHROPIC_AUTH_TOKEN over API_KEY. A stale
token forwarded from the submitting shell must never authenticate against the
configured base_url — that was a full-lane 401 outage (every executor call
failed while the OpenAI-protocol proposer lane worked). Config-pinned keys
(``executor.api_key``) are the strongest authority: they override anything
the environment carries.
"""
from __future__ import annotations

from experiment.apptainer import evaluator_environment, executor_environment


def _poisoned_environ(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-forwarded")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "STALE-other-provider-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://old-provider.example")
    monkeypatch.setenv("https_proxy", "http://proxy.example:3128")


def test_executor_config_api_key_overrides_everything(monkeypatch):
    _poisoned_environ(monkeypatch)
    env = executor_environment(
        base_url="https://api.siliconflow.cn",
        max_output_tokens=64000,
        api_key="sk-pinned",
    )
    # The pinned key is THE credential: inherited key and token both lose.
    assert env["ANTHROPIC_API_KEY"] == "sk-pinned"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.siliconflow.cn"
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_executor_base_url_pin_drops_stale_auth_token(monkeypatch):
    """No pinned key, but a pinned base_url: an inherited AUTH_TOKEN is a
    credential for some OTHER endpoint — drop it so the (forwarded) API_KEY
    authenticates against the configured endpoint."""
    _poisoned_environ(monkeypatch)
    env = executor_environment(
        base_url="https://api.siliconflow.cn", max_output_tokens=64000,
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://api.siliconflow.cn"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-forwarded"


def test_executor_without_pin_inherits_forwarded_env(monkeypatch):
    # No configured base_url and no pinned key → nothing to assert authority
    # over; the forwarded environment passes through untouched (local lane).
    _poisoned_environ(monkeypatch)
    env = executor_environment(base_url=None, max_output_tokens=64000)
    assert env["ANTHROPIC_AUTH_TOKEN"] == "STALE-other-provider-token"
    assert env["ANTHROPIC_BASE_URL"] == "https://old-provider.example"


def test_evaluator_never_sees_anthropic_credentials(monkeypatch):
    _poisoned_environ(monkeypatch)
    env = evaluator_environment()
    assert not any(k.startswith("ANTHROPIC_") for k in env)
    # Non-Anthropic forwarded vars pass through.
    assert env["https_proxy"] == "http://proxy.example:3128"
