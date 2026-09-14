"""GET /config/models — effective per-role model config with provenance (issue #73)."""

from __future__ import annotations

import pytest

from crucible.llm.registry import ModelRole

ROLES = {r.value for r in ModelRole}


def test_shape_has_all_four_roles(client):
    resp = client.get("/config/models")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["roles"]) == ROLES
    for role in ROLES:
        entry = body["roles"][role]
        assert set(entry) == {"provider", "model", "temperature", "base_url", "source"}
        assert set(entry["source"]) == {"provider", "model", "temperature", "base_url"}
        assert isinstance(entry["temperature"], float)


def test_hunter_defaults_to_deepseek(client):
    body = client.get("/config/models").json()
    hunter = body["roles"]["hunter"]
    assert hunter["provider"] == "deepseek"
    assert hunter["model"] == "deepseek-v4-flash"
    assert hunter["base_url"] == "https://api.deepseek.com"
    assert hunter["source"]["provider"] == "default"
    assert hunter["source"]["model"] == "default"


def test_env_override_is_reflected_with_provenance(client, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek-r2")
    monkeypatch.setenv("CRUCIBLE_TEMPERATURE_HUNTER", "0.9")
    body = client.get("/config/models").json()
    hunter = body["roles"]["hunter"]
    assert hunter["model"] == "deepseek-r2"
    assert hunter["temperature"] == pytest.approx(0.9)
    assert hunter["source"]["model"] == "env:CRUCIBLE_MODEL_HUNTER"
    assert hunter["source"]["temperature"] == "env:CRUCIBLE_TEMPERATURE_HUNTER"


def test_no_api_key_field_anywhere_in_response(client):
    body = client.get("/config/models").json()
    for entry in body["roles"].values():
        assert "api_key" not in entry
        assert "api_key" not in entry["source"]


CANARY = "sk-fake-LEAKCANARY"


def test_provider_api_keys_never_leak_into_the_response(client, monkeypatch):
    """Set every provider-key env var this code knows about to a canary value
    and assert it never appears anywhere in the serialised response."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", CANARY)
    monkeypatch.setenv("OPENROUTER_API_KEY", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_HUNTER", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_RECON", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_VALIDATOR_BUG", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_VALIDATOR_REACH", CANARY)
    resp = client.get("/config/models")
    assert resp.status_code == 200
    assert CANARY not in resp.text
    assert "LEAKCANARY" not in resp.text
