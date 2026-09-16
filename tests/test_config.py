"""File config: config.yaml / crucible.toml -> registry + tracing env (specs §3, §6, §13).

OpenRouter-only (issue #80): every role is implicitly OpenRouter, so config
files/overrides carry a `model` id and nothing to say about `provider`.
"""

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
        model: qwen/qwen-2.5-72b-instruct
        temperature: 0.15
      hunter:
        model: deepseek/deepseek-chat-v3.1
      validator_bug:
        model: qwen/qwen3-32b
      validator_reach:
        model: deepseek/deepseek-r1-0528
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
        for pat in ("CRUCIBLE_MODEL_{}", "CRUCIBLE_API_KEY_{}", "CRUCIBLE_TEMPERATURE_{}"):
            mp.delenv(pat.format(role), raising=False)


def test_yaml_config_populates_the_model_matrix(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    reg = load_registry(str(cfg))
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert reg.endpoint(ModelRole.VALIDATOR_BUG).model == "qwen/qwen3-32b"
    assert reg.endpoint(ModelRole.RECON).temperature == pytest.approx(0.15)


def test_env_still_overrides_yaml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(_MATRIX_YAML)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-r1-0528")
    reg = load_registry(str(cfg))
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-r1-0528"


def test_yaml_layers_over_toml_when_both_auto_discovered(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nmodel = "deepseek/deepseek-v3.2"\n'
        '[models.recon]\nmodel = "deepseek/deepseek-v3.2"\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    model: deepseek/deepseek-chat-v3.1\n"
    )
    monkeypatch.chdir(tmp_path)
    reg = load_registry(None)
    # yaml wins for hunter, toml still supplies recon
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert reg.endpoint(ModelRole.RECON).model == "deepseek/deepseek-v3.2"


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
    assert reg.endpoint(ModelRole.RECON).provider is Provider.OPENROUTER
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"


# --- provenance (issue #73) -------------------------------------------------

def test_provenance_all_default_with_no_config_and_no_env(monkeypatch):
    _clear_model_env(monkeypatch)
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    assert set(origins) == set(ModelRole)
    for role in ModelRole:
        assert origins[role] == {
            "model": "default", "temperature": "default", "base_url": "default",
        }
    # sanity: still the same registry `load_registry` would return
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"


def test_provenance_reflects_crucible_toml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nmodel = "deepseek/deepseek-v3.2"\ntemperature = 0.5\n'
    )
    monkeypatch.chdir(tmp_path)
    _, origins = load_registry_with_provenance(None)
    assert origins[ModelRole.HUNTER]["model"] == "crucible.toml"
    assert origins[ModelRole.HUNTER]["temperature"] == "crucible.toml"
    # untouched field / untouched role stay at "default"
    assert origins[ModelRole.HUNTER]["base_url"] == "default"
    assert origins[ModelRole.RECON]["model"] == "default"


def test_provenance_yaml_layers_over_toml(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.hunter]\nmodel = "deepseek/deepseek-v3.2"\ntemperature = 0.5\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    model: deepseek/deepseek-chat-v3.1\n"
    )
    monkeypatch.chdir(tmp_path)
    reg, origins = load_registry_with_provenance(None)
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert origins[ModelRole.HUNTER]["model"] == "config.yaml"
    # temperature was only set by the toml — yaml never mentioned it
    assert origins[ModelRole.HUNTER]["temperature"] == "crucible.toml"


def test_provenance_env_wins_over_file_and_names_the_var(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "models:\n  hunter:\n    model: deepseek/deepseek-v3.2\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-chat-v3.1")
    monkeypatch.setenv("CRUCIBLE_TEMPERATURE_HUNTER", "0.7")
    reg, origins = load_registry_with_provenance(None)
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert reg.endpoint(ModelRole.HUNTER).temperature == pytest.approx(0.7)
    assert origins[ModelRole.HUNTER]["model"] == "env:CRUCIBLE_MODEL_HUNTER"
    assert origins[ModelRole.HUNTER]["temperature"] == "env:CRUCIBLE_TEMPERATURE_HUNTER"


def test_provenance_full_precedence_chain_default_toml_yaml_env(tmp_path, monkeypatch):
    """default < crucible.toml < config.yaml < env, field by field."""
    _clear_model_env(monkeypatch)
    (tmp_path / "crucible.toml").write_text(
        '[models.recon]\nmodel = "deepseek/deepseek-v3.2"\ntemperature = 0.4\n'
    )
    (tmp_path / "config.yaml").write_text(
        "models:\n  recon:\n    temperature: 0.6\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CRUCIBLE_MODEL_RECON", "deepseek/deepseek-chat-v3.1")
    reg, origins = load_registry_with_provenance(None)
    e = reg.endpoint(ModelRole.RECON)
    o = origins[ModelRole.RECON]
    assert e.model == "deepseek/deepseek-chat-v3.1" and o["model"] == "env:CRUCIBLE_MODEL_RECON"
    assert e.temperature == pytest.approx(0.6) and o["temperature"] == "config.yaml"
    assert o["base_url"] == "default"


# --- per-run model overrides (issue #77) ------------------------------------

def test_run_override_layers_above_env_with_run_override_provenance(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-chat-v3.1")
    reg, origins = load_registry_with_provenance(
        "/nonexistent/config.yaml",
        run_override={"hunter": {"temperature": 0.9}},
    )
    hunter = reg.endpoint(ModelRole.HUNTER)
    assert hunter.model == "deepseek/deepseek-chat-v3.1"  # env value untouched — override didn't set model
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


def test_apply_model_override_rejects_provider_field():
    """Issue #80: no provider choice — an override with `provider` is rejected
    as an unexpected field, not resolved."""
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="unexpected field"):
        apply_model_override(endpoints, origins, {"recon": {"provider": "openrouter"}})


def test_apply_model_override_rejects_model_outside_catalog():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="catalog"):
        apply_model_override(endpoints, origins, {"recon": {"model": "not-a-catalog-model"}})


def test_apply_model_override_rejects_hunter_eq_validator_via_override_on_validator():
    """Host hunter (deepseek/deepseek-chat-v3.1) and host validator_bug
    (qwen/qwen3-32b) differ by default; overriding
    validator_bug to match hunter must 422."""
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="different models"):
        apply_model_override(
            endpoints, origins,
            {"validator_bug": {"model": "deepseek/deepseek-chat-v3.1"}},
        )


def test_apply_model_override_rejects_hunter_eq_validator_via_override_on_hunter():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    with pytest.raises(ModelOverrideError, match="different models"):
        apply_model_override(
            endpoints, origins,
            {"hunter": {"model": "qwen/qwen3-32b"}},
        )


def test_apply_model_override_accepts_a_valid_override_on_both_roles():
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    endpoints = {r: reg.endpoint(r) for r in ModelRole}
    out, out_origins = apply_model_override(
        endpoints, origins,
        {
            "hunter": {"model": "deepseek/deepseek-r1-0528"},
            "validator_bug": {"model": "qwen/qwen3-32b"},
        },
    )
    assert out[ModelRole.HUNTER].model == "deepseek/deepseek-r1-0528"
    assert out[ModelRole.VALIDATOR_BUG].model == "qwen/qwen3-32b"
    assert out_origins[ModelRole.HUNTER]["model"] == "run override"
    # untouched role/fields are unaffected
    assert out[ModelRole.RECON].model == endpoints[ModelRole.RECON].model
    assert out_origins[ModelRole.RECON]["model"] == "default"


# --- saved host-default model config (Settings tab, issue #80) -------------
#
# Applied as a layer above env vars and below a per-run override — see
# `load_registry_with_provenance`'s docstring.

def _store(tmp_path):
    from crucible.store.dao import Store

    return Store(f"sqlite:///{tmp_path}/f.sqlite")


def test_no_store_is_a_noop_not_an_error(monkeypatch):
    _clear_model_env(monkeypatch)
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml")
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"


def test_empty_store_is_a_noop(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml", store=_store(tmp_path))
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-chat-v3.1"
    assert origins[ModelRole.HUNTER]["model"] == "default"


def test_saved_override_applies_with_settings_provenance(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml", store=store)
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-r1-0528"
    assert origins[ModelRole.HUNTER]["model"] == "settings"


def test_saved_store_override_wins_over_env(tmp_path, monkeypatch):
    """settings > env in precedence — a live Settings choice is a more
    deliberate, more recent signal than a static deploy-time env var, so it
    wins. (An emergency ops override still has the per-run override, or
    clearing the saved value via Settings/DELETE, above it.)"""
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-v4-pro")
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml", store=store)
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-r1-0528"
    assert origins[ModelRole.HUNTER]["model"] == "settings"


def test_run_override_still_wins_over_saved_store_override(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    reg, origins = load_registry_with_provenance(
        "/nonexistent/config.yaml", run_override={"hunter": {"model": "deepseek/deepseek-v4-pro"}},
        store=store,
    )
    assert reg.endpoint(ModelRole.HUNTER).model == "deepseek/deepseek-v4-pro"
    assert origins[ModelRole.HUNTER]["model"] == "run override"


def test_saved_override_on_unrelated_role_leaves_others_at_default(tmp_path, monkeypatch):
    _clear_model_env(monkeypatch)
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    reg, origins = load_registry_with_provenance("/nonexistent/config.yaml", store=store)
    assert origins[ModelRole.RECON]["model"] == "default"
    assert origins[ModelRole.VALIDATOR_BUG]["model"] == "default"
