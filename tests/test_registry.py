"""Registry: hunter != validator assertion, OpenRouter-only wiring (specs.md §6, issue #80)."""

import pytest

from crucible.llm.registry import ModelEndpoint, ModelRegistry, ModelRole, Provider


def _ep(role, model):
    return ModelEndpoint(role=role, model=model)


def _endpoints(hunter="deepseek/deepseek-chat-v3.1", validator_bug="qwen/qwen3-32b"):
    return {
        ModelRole.RECON: _ep(ModelRole.RECON, "qwen/qwen-2.5-72b-instruct"),
        ModelRole.HUNTER: _ep(ModelRole.HUNTER, hunter),
        ModelRole.VALIDATOR_BUG: _ep(ModelRole.VALIDATOR_BUG, validator_bug),
        ModelRole.VALIDATOR_REACH: _ep(ModelRole.VALIDATOR_REACH, "deepseek/deepseek-r1-0528"),
    }


def test_identical_hunter_and_validator_fails_loudly():
    same = "deepseek/deepseek-chat-v3.1"
    with pytest.raises(RuntimeError, match="different models"):
        ModelRegistry(_endpoints(hunter=same, validator_bug=same))


def test_missing_role_rejected():
    eps = _endpoints()
    del eps[ModelRole.VALIDATOR_REACH]
    with pytest.raises(ValueError, match="missing endpoints"):
        ModelRegistry(eps)


def test_distinct_models_ok_and_sampling_recorded():
    reg = ModelRegistry(_endpoints())
    params = reg.sampling_params(ModelRole.HUNTER)
    assert params["model"] == "deepseek/deepseek-chat-v3.1"
    assert params["provider"] == "openrouter"
    assert "temperature" in params and "num_ctx" in params


def test_default_config_registry_builds():
    from crucible.config import load_registry

    reg = load_registry(config_path="/nonexistent.toml")  # falls back to DEFAULT_ENDPOINTS
    assert reg.endpoint(ModelRole.HUNTER).model != reg.endpoint(ModelRole.VALIDATOR_BUG).model


def test_unknown_provider_value_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown LLM provider 'ollama'"):
        Provider.parse("ollama")


# --- OpenRouter --------------------------------------------------------


def test_openrouter_provider_builds_chat_model():
    eps = _endpoints()
    eps[ModelRole.HUNTER] = ModelEndpoint(
        role=ModelRole.HUNTER, model="deepseek/deepseek-chat-v3.1",
        api_key="sk-or-test", num_predict=2048,
    )
    reg = ModelRegistry(eps)
    m = reg.chat_model(ModelRole.HUNTER)
    assert type(m).__name__ == "ChatOpenAI"
    assert "openrouter.ai" in str(m.openai_api_base)
    sp = reg.sampling_params(ModelRole.HUNTER)
    assert sp["provider"] == "openrouter"
    assert sp["model"] == "deepseek/deepseek-chat-v3.1"
    assert sp["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    reg = ModelRegistry(_endpoints())
    with pytest.raises(RuntimeError, match="needs an API key"):
        reg.chat_model(ModelRole.HUNTER)


def test_openrouter_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    reg = ModelRegistry(_endpoints())
    assert type(reg.chat_model(ModelRole.VALIDATOR_REACH)).__name__ == "ChatOpenAI"


def test_per_role_model_env_wins(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-r1-0528")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-r1-0528"


def test_openrouter_matrix_from_env(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example/api/v1")
    for role, model in (
        ("RECON", "qwen/qwen-2.5-72b-instruct"),
        ("HUNTER", "deepseek/deepseek-chat-v3.1"),
        ("VALIDATOR_BUG", "qwen/qwen3-32b"),
        ("VALIDATOR_REACH", "deepseek/deepseek-r1-0528"),
    ):
        monkeypatch.setenv(f"CRUCIBLE_MODEL_{role}", model)
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.HUNTER).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert reg.endpoint(ModelRole.RECON).resolved_base_url() == "https://proxy.example/api/v1"


def test_openrouter_hunter_ne_validator_still_enforced(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    for role in ("HUNTER", "VALIDATOR_BUG"):
        monkeypatch.setenv(f"CRUCIBLE_MODEL_{role}", "deepseek/deepseek-chat-v3.1")
    with pytest.raises(RuntimeError, match="different models"):
        load_registry("/nonexistent.toml")
