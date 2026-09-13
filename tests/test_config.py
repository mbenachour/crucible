"""File config: config.yaml / crucible.toml -> registry + tracing env (specs §3, §6, §13)."""

import textwrap

import pytest

from crucible.config import apply_file_tracing_env, load_registry, load_registry_with_provenance
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
