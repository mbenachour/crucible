"""File config: config.yaml / crucible.toml -> registry + tracing env (specs §3, §6, §13)."""

import textwrap

import pytest

from crucible.config import (
    ModelOverrideError,
    apply_file_tracing_env,
    apply_model_override,
    load_registry,
    load_registry_with_provenance,
)
from crucible.llm.registry import ModelRole, Provider

_MATRIX_YAML = textwrap.dedent(
    """
    models:
      recon:
        provider: openrouter
        model: qwen/qwen-2.5-coder-32b-instruct
        temperature: 0.15
      hunter:
        provider: openrouter
        model: anthropic/claude-sonnet-4
      validator_bug:
        provider: openrouter
        model: openai/gpt-4o
      validator_reach:
        provider: openrouter
        model: google/gemini-2.0-flash
    tracing:
      langsmith:
        enabled: true
        project: crucible-test
      otel:
        enabled: true
        endpoint: http://collector.example:4318
    """
)


def _clear_model_env(mp):
    for role in ("RECON", "HUNTER", "VALIDATOR_BUG", "VALIDATOR_REACH"):
        for pat in ("{}_LLM", "CRUCIBLE_PROVIDER_{}", "CRUCIBLE_MODEL_{}", "CRUCIBLE_API_KEY_{}"):
            mp.delenv(pat.format(role), raising=False)


def test_yaml_config_populates_the_model_matrix(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    reg = load_registry(str(cfg))
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "anthropic/claude-sonnet-4"
    assert reg.endpoint(ModelRole.VALIDATOR_BUG).model == "openai/gpt-4o"
    assert reg.endpoint(ModelRole.RECON).temperature == pytest.approx(0.15)


def test_env_still_overrides_yaml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "meta-llama/llama-3.3-70b-instruct")
    reg = load_registry(str(cfg))
    assert reg.endpoint(ModelRole.HUNTER).model == "meta-llama/llama-3.3-70b-instruct"


def test_yaml_layers_over_toml_when_both_auto_discovered(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nprovider = "deepseek"\nmodel = "deepseek-chat"\n'
        '[models.recon]\nprovider = "deepseek"\nmodel = "deepseek-chat"\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    provider: openrouter\n    model: anthropic/claude-sonnet-4\n"
    )
    monkeypatch.chdir(tmp_path)
    reg = load_registry(None)
    # yaml wins for hunter, toml still supplies recon
    assert reg.endpoint(ModelRole.HUNTER).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "anthropic/claude-sonnet-4"
    assert reg.endpoint(ModelRole.RECON).provider is Provider.DEEPSEEK


def test_apply_file_tracing_env_sets_defaults(tmp_path, monkeypatch):
    for v in (
        "LANGSMITH_TRACING", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT",
        "CRUCIBLE_OTEL", "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        monkeypatch.delenv(v, raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    apply_file_tracing_env(str(cfg))
    import os

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "crucible-test"
    assert os.environ["CRUCIBLE_OTEL"] == "1"
    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector.example:4318"


def test_apply_file_tracing_env_does_not_clobber_real_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_PROJECT", "from-real-env")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    apply_file_tracing_env(str(cfg))
    import os

    assert os.environ["LANGSMITH_PROJECT"] == "from-real-env"  # env wins over the file


def test_no_config_file_is_a_noop(monkeypatch):
    _clear_model_env(monkeypatch)
    reg = load_registry("/nonexistent/config.yaml")
    # built-in DEFAULT_ENDPOINTS unchanged
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OLLAMA
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek-v4-flash"


# --- provenance (issue #73) -------------------------------------------------

def test_provenance_all_default_with_no_config_and_no_env(monkeypatch):
    _clear_model_env(monkeypatch)
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    assert set(origins) == set(ModelRole)
    for role in ModelRole:
        assert origins[role] == {
            "provider": "default", "model": "default",
            "temperature": "default", "base_url": "default",
        }
    # sanity: still the same registry `load_registry` would return
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek-v4-flash"


def test_provenance_reflects_crucible_toml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nprovider = "deepseek"\nmodel = "deepseek-chat"\ntemperature = 0.5\n'
    )
    monkeypatch.chdir(tmp_path)
    _, origins = load_registry_with_provenance(None)
    assert origins[ModelRole.HUNTER]["model"] == "crucible.toml"
    assert origins[ModelRole.HUNTER]["provider"] == "crucible.toml"
    assert origins[ModelRole.HUNTER]["temperature"] == "crucible.toml"
    # untouched field / untouched role stay at "default"
    assert origins[ModelRole.HUNTER]["base_url"] == "default"
    assert origins[ModelRole.RECON]["model"] == "default"


def test_provenance_yaml_layers_over_toml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nprovider = "deepseek"\nmodel = "deepseek-chat"\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    model: anthropic/claude-sonnet-4\n"
    )
    monkeypatch.chdir(tmp_path)
    reg, origins = load_registry_with_provenance(None)
    assert reg.endpoint(ModelRole.HUNTER).model == "anthropic/claude-sonnet-4"
    assert origins[ModelRole.HUNTER]["model"] == "config.yaml"
    # provider was only set by the toml — yaml never mentioned it
    assert origins[ModelRole.HUNTER]["provider"] == "crucible.toml"


def test_provenance_env_wins_over_file_and_names_the_var(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    provider: deepseek\n    model: deepseek-chat\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek-v4-flash")
    monkeypatch.setenv("CRUCIBLE_TEMPERATURE_HUNTER", "0.7")
    reg, origins = load_registry_with_provenance(None)
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek-v4-flash"
    assert reg.endpoint(ModelRole.HUNTER).temperature == pytest.approx(0.7)
    assert origins[ModelRole.HUNTER]["model"] == "env:CRUCIBLE_MODEL_HUNTER"
    assert origins[ModelRole.HUNTER]["temperature"] == "env:CRUCIBLE_TEMPERATURE_HUNTER"
    # provider came from the file and env never touched it
    assert origins[ModelRole.HUNTER]["provider"] == "config.yaml"


def test_provenance_full_precedence_chain_default_toml_yaml_env(tmp_path, monkeypatch):
    """default < crucible.toml < config.yaml < env, field by field."""
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.recon]\nprovider = "deepseek"\nmodel = "deepseek-chat"\ntemperature = 0.4\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  recon:\n    temperature: 0.6\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRUCIBLE_MODEL_RECON", "deepseek-v4-flash")
    reg, origins = load_registry_with_provenance(None)
    e = reg.endpoint(ModelRole.RECON)
    o = origins[ModelRole.RECON]
    assert e.provider is Provider.DEEPSEEK and o["provider"] == "crucible.toml"
    assert e.model == "deepseek-v4-flash" and o["model"] == "env:CRUCIBLE_MODEL_RECON"
    assert e.temperature == pytest.approx(0.6) and o["temperature"] == "config.yaml"
    assert o["base_url"] == "default"


# --- per-run model overrides (issue #77) ------------------------------------

def test_run_override_layers_above_env_with_run_override_provenance(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek-chat")
    reg, origins = load_registry_with_provenance(
        "/nonexistent/config.yaml",
        run_override={"hunter": {"temperature": 0.9}},
    )
    hunter = reg.endpoint(ModelRole.HUNTER)
    assert hunter.model == "deepseek-chat"  # env value untouched — override didn't set model
    assert hunter.temperature == pytest.approx(0.9)
    assert origins[ModelRole.HUNTER]["model"] == "env:CRUCIBLE_MODEL_HUNTER"
    assert origins[ModelRole.HUNTER]["temperature"] == "run override"


def test_run_override_with_no_fields_touched_is_a_noop():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml", run_override=None)
    assert origins[ModelRole.RECON]["model"] == "default"
    reg2, origins2 = load_registry_with_provenance("/nonexistent/config.yaml", run_override={})
    assert reg2.endpoint(ModelRole.RECON).model == reg.endpoint(ModelRole.RECON).model
    assert origins2 == origins


def test_apply_model_override_unknown_role_rejected():
    _, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    with pytest.raises(ModelOverrideError, match="unknown model role"):
        apply_model_override(
            {r: None for r in ModelRole}, origins, {"not_a_role": {"model": "x"}},
        )


def test_apply_model_override_openrouter_switch_without_model_is_unresolvable():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="openrouter"):
        apply_model_override(endpoints, origins, {"recon": {"provider": "openrouter"}})


def test_apply_model_override_bad_provider_name_is_unresolvable():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="unknown LLM provider"):
        apply_model_override(endpoints, origins, {"recon": {"provider": "not-a-provider"}})


def test_apply_model_override_deepseek_switch_falls_back_to_default_model():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    out, out_origins = apply_model_override(endpoints, origins, {"recon": {"provider": "deepseek"}})
    assert out[ModelRole.RECON].model == "deepseek-v4-flash"
    assert out_origins[ModelRole.RECON]["provider"] == "run override"
    assert out_origins[ModelRole.RECON]["model"] == "default"  # not explicitly set by the override


def test_apply_model_override_rejects_hunter_eq_validator_via_override_on_validator():
    """Host hunter (deepseek-v4-flash) and host validator_bug (llama3.1:8b)
    differ by default; overriding validator_bug to match hunter must 422."""
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="different models"):
        apply_model_override(
            endpoints, origins,
            {"validator_bug": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
        )


def test_apply_model_override_rejects_hunter_eq_validator_via_override_on_hunter():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="different models"):
        apply_model_override(
            endpoints, origins,
            {"hunter": {"provider": "ollama", "model": "llama3.1:8b"}},
        )


def test_apply_model_override_accepts_a_valid_override_on_both_roles():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    out, out_origins = apply_model_override(
        endpoints, origins,
        {
            "hunter": {"provider": "deepseek", "model": "deepseek-chat"},
            "validator_bug": {"provider": "deepseek", "model": "deepseek-reasoner"},
        },
    )
    assert out[ModelRole.HUNTER].model == "deepseek-chat"
    assert out[ModelRole.VALIDATOR_BUG].model == "deepseek-reasoner"
    assert out_origins[ModelRole.HUNTER]["model"] == "run override"
    # untouched role/fields are unaffected
    assert out[ModelRole.RECON].model == endpoints[ModelRole.RECON].model
    assert out_origins[ModelRole.RECON]["model"] == "default"
