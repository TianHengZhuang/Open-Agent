import pytest
from fastapi import HTTPException

from openagent.api.v1.koro import KoroConfig, _chat_config


def config(**overrides) -> KoroConfig:
    values = {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "timeout": 30.0,
        "api_key": "sk-stored",
        "local": False,
        "provider": "OpenAI",
        "customized": True,
    }
    values.update(overrides)
    return KoroConfig(**values)


def test_model_override_keeps_endpoint_and_key():
    resolved = _chat_config(config(), "gpt-4.1-mini", None)
    assert resolved.model == "gpt-4.1-mini"
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.api_key == "sk-stored"
    assert resolved.provider == "OpenAI"


def test_local_endpoint_override_never_sends_the_stored_key():
    resolved = _chat_config(config(), "llama3.2:3b", "http://127.0.0.1:1234/v1/")
    assert resolved.base_url == "http://127.0.0.1:1234/v1"
    assert resolved.model == "llama3.2:3b"
    assert resolved.api_key == ""
    assert resolved.local is True
    assert resolved.provider == "LM Studio"


def test_hosted_endpoint_override_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _chat_config(config(), None, "https://api.groq.com/openai/v1")
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "KORO_OVERRIDE_NOT_LOCAL"


def test_override_requires_an_http_url():
    with pytest.raises(HTTPException) as excinfo:
        _chat_config(config(), None, "localhost:11434")
    assert excinfo.value.detail["code"] == "KORO_OVERRIDE_INVALID"


def test_no_override_returns_the_configured_endpoint():
    base = config()
    assert _chat_config(base, None, None) == base
    assert _chat_config(base, "   ", "") == base
