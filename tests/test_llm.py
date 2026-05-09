"""Provider registry and resolution tests for openkite.llm.

These tests never import optional provider packages or hit any real API. We
exercise the resolution logic, the missing-key error path, and the combined
``provider:model`` form.
"""

from __future__ import annotations

import pytest

from openkite.llm import PROVIDERS, _resolve, build_llm


def _clear_env(monkeypatch):
    for var in ("OPENKITE_PROVIDER", "OPENKITE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    for prov in PROVIDERS.values():
        if prov.env_key:
            monkeypatch.delenv(prov.env_key, raising=False)


def test_default_provider_and_model(monkeypatch):
    _clear_env(monkeypatch)
    prov, model = _resolve(provider=None, model=None)
    assert prov.name == "anthropic"
    assert model == prov.default_model


def test_env_overrides_default(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENKITE_PROVIDER", "openai")
    monkeypatch.setenv("OPENKITE_MODEL", "gpt-4o")
    prov, model = _resolve(None, None)
    assert prov.name == "openai"
    assert model == "gpt-4o"


def test_combined_form_overrides_provider(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENKITE_PROVIDER", "anthropic")  # ignored
    prov, model = _resolve(None, "openai:gpt-4o-mini")
    assert prov.name == "openai"
    assert model == "gpt-4o-mini"


def test_unknown_provider_raises(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(ValueError, match="Unknown provider"):
        _resolve("not-a-provider", None)


def test_missing_api_key_raises(monkeypatch):
    _clear_env(monkeypatch)
    with pytest.raises(OSError, match="ANTHROPIC_API_KEY"):
        build_llm(provider="anthropic", model="claude-haiku-4-5-20251001")


def test_provider_default_model_used_when_model_blank(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("OPENKITE_PROVIDER", "groq")
    prov, model = _resolve(None, None)
    assert prov.name == "groq"
    assert model == PROVIDERS["groq"].default_model


def test_local_provider_skips_env_check(monkeypatch, mocker):
    _clear_env(monkeypatch)
    fake_chat = mocker.MagicMock()
    init = mocker.patch("langchain.chat_models.init_chat_model", return_value=fake_chat)
    out = build_llm(provider="ollama", model="llama3.1")
    assert out is fake_chat
    init.assert_called_once()
    assert init.call_args.args[0] == "ollama:llama3.1"
