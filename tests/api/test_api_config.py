"""GET /config/models — effective per-role model config with provenance (issue #73)."""

from __future__ import annotations

import pytest

from crucible.llm.registry import ModelRole
from tests.api.conftest import RUN_A

ROLES = {r.value for r in ModelRole}


def test_shape_has_all_four_roles(client):
    resp = client.get("/config/models")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["roles"]) == ROLES
    for role in ROLES:
        entry = body["roles"][role]
        assert set(entry) == {"provider", "model", "temperature", "base_url", "source"}
        assert entry["provider"] == "openrouter"
        assert set(entry["source"]) == {"model", "temperature", "base_url"}
        assert isinstance(entry["temperature"], float)


def test_hunter_defaults_to_deepseek(client):
    body = client.get("/config/models").json()
    hunter = body["roles"]["hunter"]
    assert hunter["provider"] == "openrouter"
    assert hunter["model"] == "deepseek/deepseek-chat-v3.1"
    assert hunter["base_url"] == "https://openrouter.ai/api/v1"
    assert hunter["source"]["model"] == "default"


def test_env_override_is_reflected_with_provenance(client, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_MODEL_HUNTER", "deepseek/deepseek-r1-0528")
    monkeypatch.setenv("CRUCIBLE_TEMPERATURE_HUNTER", "0.9")
    body = client.get("/config/models").json()
    hunter = body["roles"]["hunter"]
    assert hunter["model"] == "deepseek/deepseek-r1-0528"
    assert hunter["temperature"] == pytest.approx(0.9)
    assert hunter["source"]["model"] == "env:CRUCIBLE_MODEL_HUNTER"
    assert hunter["source"]["temperature"] == "env:CRUCIBLE_TEMPERATURE_HUNTER"


def test_no_api_key_field_anywhere_in_response(client):
    body = client.get("/config/models").json()
    for entry in body["roles"].values():
        assert "api_key" not in entry
        assert "api_key" not in entry["source"]


CANARY = "sk-fake-LEAKCANARY"


# --- run_id / run override provenance (issue #77) --------------------------

def test_run_id_with_no_override_is_identical_to_the_hostwide_response(client):
    plain = client.get("/config/models").json()
    scoped = client.get("/config/models", params={"run_id": RUN_A}).json()
    assert scoped == plain


def test_run_id_reports_run_override_provenance(client, store):
    store.create_launch(
        "ov-run", "octocat/Hello-World",
        model_override={"hunter": {"model": "deepseek/deepseek-r1-0528"}},
    )
    body = client.get("/config/models", params={"run_id": "ov-run"}).json()
    hunter = body["roles"]["hunter"]
    assert hunter["model"] == "deepseek/deepseek-r1-0528"
    assert hunter["source"]["model"] == "run override"
    # untouched field / untouched role fall back to the host-effective value
    assert hunter["source"]["temperature"] == "default"
    assert body["roles"]["recon"]["source"]["model"] == "default"


def test_run_id_unknown_run_is_404(client):
    r = client.get("/config/models", params={"run_id": "does-not-exist"})
    assert r.status_code == 404


def test_provider_api_keys_never_leak_into_the_response(client, monkeypatch):
    """Set every provider-key env var this code knows about to a canary value
    and assert it never appears anywhere in the serialised response."""
    monkeypatch.setenv("OPENROUTER_API_KEY", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_HUNTER", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_RECON", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_VALIDATOR_BUG", CANARY)
    monkeypatch.setenv("CRUCIBLE_API_KEY_VALIDATOR_REACH", CANARY)
    resp = client.get("/config/models")
    assert resp.status_code == 200
    assert CANARY not in resp.text
    assert "LEAKCANARY" not in resp.text


# --- GET /config/catalog (issue #80) ----------------------------------------

def test_catalog_has_deepseek_and_qwen_families(client):
    resp = client.get("/config/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["families"]) == {"deepseek", "qwen"}
    for family, models in body["families"].items():
        assert models  # non-empty
        for m in models:
            assert m["family"] == family
            assert m["id"].startswith(f"{family}/")
            assert m["label"]
