"""Config-pinned API keys beat the submitting shell's environment."""
from __future__ import annotations

import pytest

from proposer.model import ModelError, resolve_api_key


def test_config_api_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert resolve_api_key(
        {"api_key": "sk-from-config"}, "OPENAI_API_KEY", provider="openai",
    ) == "sk-from-config"


def test_environment_fallback_when_config_has_no_key(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-zhipu")
    assert resolve_api_key({}, "ZHIPU_API_KEY", provider="zhipu") == "sk-zhipu"


def test_first_environment_variable_in_order_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert resolve_api_key(
        {}, "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
        provider="anthropic",
    ) == "token"


def test_blank_config_key_falls_through_to_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert resolve_api_key(
        {"api_key": "   "}, "OPENAI_API_KEY", provider="openai",
    ) == "sk-from-env"


def test_missing_everywhere_names_all_options(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelError, match="researcher.api_key.*OPENAI_API_KEY"):
        resolve_api_key({}, "OPENAI_API_KEY", provider="openai")
