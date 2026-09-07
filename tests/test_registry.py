"""Registry: hunter != validator assertion, provider wiring (specs.md §6, §14.3)."""

import pytest

from crucible.llm.registry import ModelEndpoint, ModelRegistry, ModelRole, Provider


def _ep(role, model, provider=Provider.OLLAMA):
    return ModelEndpoint(role=role, model=model, provider=provider)


def _endpoints(hunter="qwen2.5-coder:7b", validator_bug="llama3.1:8b"):
    return {
        ModelRole.RECON: _ep(ModelRole.RECON, "qwen2.5-coder:7b"),
        ModelRole.HUNTER: _ep(ModelRole.HUNTER, hunter),
        ModelRole.VALIDATOR_BUG: _ep(ModelRole.VALIDATOR_BUG, validator_bug),
        ModelRole.VALIDATOR_REACH: _ep(ModelRole.VALIDATOR_REACH, "llama3.1:8b"),
    }


def test_identical_hunter_and_validator_fails_loudly():
    with pytest.raises(RuntimeError, match="different models"):
        ModelRegistry(_endpoints(hunter="llama3.1:8b", validator_bug="llama3.1:8b"))


def test_missing_role_rejected():
    eps = _endpoints()
    del eps[ModelRole.VALIDATOR_REACH]
    with pytest.raises(ValueError, match="missing endpoints"):
        ModelRegistry(eps)


def test_distinct_models_ok_and_sampling_recorded():
    reg = ModelRegistry(_endpoints())
    params = reg.sampling_params(ModelRole.HUNTER)
    assert params["model"] == "qwen2.5-coder:7b"
    assert params["provider"] == "ollama"
    assert "temperature" in params and "num_ctx" in params


def test_reserved_provider_raises_on_chat_model():
    eps = _endpoints()
    eps[ModelRole.RECON] = _ep(ModelRole.RECON, "x", provider=Provider.VLLM)
    reg = ModelRegistry(eps)
    with pytest.raises(NotImplementedError, match="reserved but not wired"):
        reg.chat_model(ModelRole.RECON)


def test_deepseek_provider_builds_chat_model():
    eps = _endpoints()
    eps[ModelRole.HUNTER] = ModelEndpoint(
        role=ModelRole.HUNTER, model="deepseek-chat",
        provider=Provider.DEEPSEEK, api_key="sk-test", num_predict=2048,
    )
    reg = ModelRegistry(eps)
    m = reg.chat_model(ModelRole.HUNTER)
    assert type(m).__name__ == "ChatDeepSeek"
    assert "deepseek.com" in (getattr(m, "api_base", "") or "")
    assert reg.sampling_params(ModelRole.HUNTER)["provider"] == "deepseek"
    assert reg.sampling_params(ModelRole.HUNTER)["base_url"] == "https://api.deepseek.com"


def test_deepseek_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    eps = _endpoints()
    eps[ModelRole.HUNTER] = ModelEndpoint(
        role=ModelRole.HUNTER, model="deepseek-chat", provider=Provider.DEEPSEEK
    )
    reg = ModelRegistry(eps)
    with pytest.raises(RuntimeError, match="needs an API key"):
        reg.chat_model(ModelRole.HUNTER)


def test_deepseek_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    eps = _endpoints()
    eps[ModelRole.VALIDATOR_REACH] = ModelEndpoint(
        role=ModelRole.VALIDATOR_REACH, model="deepseek-reasoner", provider=Provider.DEEPSEEK
    )
    reg = ModelRegistry(eps)
    assert type(reg.chat_model(ModelRole.VALIDATOR_REACH)).__name__ == "ChatDeepSeek"


def test_default_config_registry_builds():
    from crucible.config import load_registry

    reg = load_registry(config_path="/nonexistent.toml")  # falls back to DEFAULT_ENDPOINTS
    assert reg.endpoint(ModelRole.HUNTER).model != reg.endpoint(ModelRole.VALIDATOR_BUG).model
