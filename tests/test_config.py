"""File config: config.yaml / crucible.toml -> registry + tracing env (specs §3, §6, §13)."""

import textwrap

import pytest

from crucible.config import apply_file_tracing_env, load_registry
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
