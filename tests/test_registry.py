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


def test_role_llm_alias_selects_provider(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("RECON_LLM", "deepseek")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.RECON).provider is Provider.DEEPSEEK


def test_crucible_provider_env_wins_over_role_llm_alias(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("RECON_LLM", "deepseek")
    monkeypatch.setenv("CRUCIBLE_PROVIDER_RECON", "ollama")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OLLAMA


def test_unknown_provider_value_is_a_clear_error(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("HUNTER_LLM", "bogus")
    with pytest.raises(ValueError, match="unknown LLM provider 'bogus'"):
        load_registry("/nonexistent.toml")


def test_openai_accepted_but_not_wired(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("RECON_LLM", "openai")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OPENAI
    with pytest.raises(NotImplementedError, match="reserved but not wired"):
        reg.chat_model(ModelRole.RECON)


def test_switching_to_deepseek_uses_deepseek_default_model(monkeypatch):
    from crucible.config import load_registry
    from crucible.llm.registry import DEFAULT_DEEPSEEK_MODEL

    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("RECON_LLM", "deepseek")
    reg = load_registry("/nonexistent.toml")
    # not the leftover ollama default
    assert reg.endpoint(ModelRole.RECON).model == DEFAULT_DEEPSEEK_MODEL


def test_deepseek_model_env_sets_the_model(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("RECON_LLM", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.RECON).model == "deepseek-v4-pro"


def test_per_role_model_wins_over_deepseek_model(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("HUNTER_LLM", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek-reasoner")
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek-reasoner"


# --- OpenRouter (the model matrix) ------------------------------------


def test_openrouter_provider_builds_chat_model():
    eps = _endpoints()
    eps[ModelRole.HUNTER] = ModelEndpoint(
        role=ModelRole.HUNTER, model="anthropic/claude-sonnet-4",
        provider=Provider.OPENROUTER, api_key="sk-or-test", num_predict=2048,
    )
    reg = ModelRegistry(eps)
    m = reg.chat_model(ModelRole.HUNTER)
    assert type(m).__name__ == "ChatOpenAI"
    assert "openrouter.ai" in str(m.openai_api_base)
    sp = reg.sampling_params(ModelRole.HUNTER)
    assert sp["provider"] == "openrouter"
    assert sp["model"] == "anthropic/claude-sonnet-4"
    assert sp["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    eps = _endpoints()
    eps[ModelRole.HUNTER] = ModelEndpoint(
        role=ModelRole.HUNTER, model="anthropic/claude-sonnet-4",
        provider=Provider.OPENROUTER,
    )
    reg = ModelRegistry(eps)
    with pytest.raises(RuntimeError, match="needs an API key"):
        reg.chat_model(ModelRole.HUNTER)


def test_openrouter_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    eps = _endpoints()
    eps[ModelRole.VALIDATOR_REACH] = ModelEndpoint(
        role=ModelRole.VALIDATOR_REACH, model="google/gemini-2.0-flash",
        provider=Provider.OPENROUTER,
    )
    reg = ModelRegistry(eps)
    assert type(reg.chat_model(ModelRole.VALIDATOR_REACH)).__name__ == "ChatOpenAI"


def test_openrouter_role_needs_explicit_model(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("RECON_LLM", "openrouter")
    monkeypatch.delenv("CRUCIBLE_MODEL_RECON", raising=False)
    with pytest.raises(ValueError, match="routed to OpenRouter but no model"):
        load_registry("/nonexistent.toml")


def test_openrouter_matrix_from_env(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example/api/v1")
    for role, model in (
        ("RECON", "qwen/qwen-2.5-coder-32b-instruct"),
        ("HUNTER", "anthropic/claude-sonnet-4"),
        ("VALIDATOR_BUG", "openai/gpt-4o"),
        ("VALIDATOR_REACH", "google/gemini-2.0-flash"),
    ):
        monkeypatch.setenv(f"{role}_LLM", "openrouter")
        monkeypatch.setenv(f"CRUCIBLE_MODEL_{role}", model)
    reg = load_registry("/nonexistent.toml")
    assert reg.endpoint(ModelRole.HUNTER).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "anthropic/claude-sonnet-4"
    assert reg.endpoint(ModelRole.RECON).resolved_base_url() == "https://proxy.example/api/v1"


def test_openrouter_hunter_ne_validator_still_enforced(monkeypatch):
    from crucible.config import load_registry

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    for role in ("HUNTER", "VALIDATOR_BUG"):
        monkeypatch.setenv(f"{role}_LLM", "openrouter")
        monkeypatch.setenv(f"CRUCIBLE_MODEL_{role}", "anthropic/claude-sonnet-4")
    with pytest.raises(RuntimeError, match="different models"):
        load_registry("/nonexistent.toml")
