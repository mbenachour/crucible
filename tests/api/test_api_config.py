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

# OpenRouter's id prefix per family — GLM is hosted under "z-ai/", not "glm/".
_ID_PREFIX = {"deepseek": "deepseek/", "qwen": "qwen/", "glm": "z-ai/"}


def test_catalog_has_deepseek_qwen_and_glm_families_with_a_size_spread(client):
    resp = client.get("/config/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["families"]) == {"deepseek", "qwen", "glm"}
    for family, models in body["families"].items():
        assert models  # non-empty
        assert {m["size"] for m in models} >= {"small"}  # every family has at least a small tier
        for m in models:
            assert m["family"] == family
            assert m["id"].startswith(_ID_PREFIX[family])
            assert m["label"]
            assert m["size"] in {"small", "mid", "big"}


# --- PUT /config/models — save a host-default override (Settings, issue #80) --

def test_put_saves_and_get_reflects_it_with_settings_provenance(client):
    r = client.put("/config/models", json={"models": {"hunter": {"model": "deepseek/deepseek-r1-0528"}}})
    assert r.status_code == 200, r.text
    hunter = r.json()["roles"]["hunter"]
    assert hunter["model"] == "deepseek/deepseek-r1-0528"
    assert hunter["source"]["model"] == "settings"

    again = client.get("/config/models").json()["roles"]["hunter"]
    assert again["model"] == "deepseek/deepseek-r1-0528"
    assert again["source"]["model"] == "settings"


def test_put_unknown_role_is_422(client):
    r = client.put("/config/models", json={"models": {"not_a_role": {"model": "x"}}})
    assert r.status_code == 422
    assert "unknown model role" in r.json()["detail"]


def test_put_model_outside_catalog_is_422_and_not_persisted(client):
    r = client.put("/config/models", json={"models": {"recon": {"model": "not-a-catalog-model"}}})
    assert r.status_code == 422
    assert "catalog" in r.json()["detail"]
    assert client.get("/config/models").json()["roles"]["recon"]["source"]["model"] == "default"


def test_put_provider_field_is_rejected(client):
    """Issue #80: no provider choice — every role is OpenRouter."""
    r = client.put("/config/models", json={"models": {"recon": {"provider": "openrouter"}}})
    assert r.status_code == 422
    assert "unexpected field" in r.json()["detail"]


def test_put_rejects_direct_hunter_eq_validator_bug_collision(client):
    same = "deepseek/deepseek-r1-0528"
    client.put("/config/models", json={"models": {"hunter": {"model": same}}})
    r = client.put("/config/models", json={"models": {"validator_bug": {"model": same}}})
    assert r.status_code == 422
    assert "different models" in r.json()["detail"]
    # the earlier, valid hunter save is untouched by the rejected request
    assert client.get("/config/models").json()["roles"]["hunter"]["model"] == same


def test_put_rejects_a_clear_that_would_reveal_a_collision(client):
    """Clearing a role can itself produce a HUNTER == VALIDATOR_BUG collision
    against another role's still-saved override — must be caught even though
    the request only *removes* a value, never sets one to match directly."""
    defaults = client.get("/config/models").json()["roles"]
    hunter_default = defaults["hunter"]["model"]

    # give hunter a different saved override so there's no collision yet
    client.put("/config/models", json={"models": {"hunter": {"model": "deepseek/deepseek-v4-pro"}}})
    # save validator_bug to exactly hunter's DEFAULT — fine right now
    ok = client.put("/config/models", json={"models": {"validator_bug": {"model": hunter_default}}})
    assert ok.status_code == 200, ok.text

    # clearing hunter reverts it to its default, which now collides
    r = client.put("/config/models", json={"models": {"hunter": None}})
    assert r.status_code == 422
    assert "different models" in r.json()["detail"]
    # the rejected clear did not partially apply — hunter keeps its override
    assert client.get("/config/models").json()["roles"]["hunter"]["model"] == "deepseek/deepseek-v4-pro"


def test_put_clear_reverts_to_default(client):
    client.put("/config/models", json={"models": {"hunter": {"model": "deepseek/deepseek-r1-0528"}}})
    r = client.put("/config/models", json={"models": {"hunter": None}})
    assert r.status_code == 200, r.text
    hunter = r.json()["roles"]["hunter"]
    assert hunter["source"]["model"] == "default"


def test_put_a_role_left_out_of_the_map_is_untouched(client):
    client.put("/config/models", json={"models": {"hunter": {"model": "deepseek/deepseek-r1-0528"}}})
    r = client.put("/config/models", json={"models": {"recon": {"model": "qwen/qwen3-32b"}}})
    assert r.status_code == 200, r.text
    assert r.json()["roles"]["hunter"]["model"] == "deepseek/deepseek-r1-0528"
    assert r.json()["roles"]["hunter"]["source"]["model"] == "settings"


def test_put_missing_body_is_422(client):
    assert client.put("/config/models", json={}).status_code == 422


def test_put_requires_write_token_when_auth_enabled(store, workspace, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    settings = ApiSettings(
        store_url=str(store.engine.url), checkpoint_db=str(tmp_path / "c2.sqlite"),
        workspace_root=str(tmp_path / "ws_a"), serve_ui=False,
        auth_token="full", auth_token_readonly="ro",
    )
    app = create_app(settings)
    app.state.store = store
    c = TestClient(app, raise_server_exceptions=False)

    body = {"models": {"hunter": {"model": "deepseek/deepseek-r1-0528"}}}
    # the router-level require_read dependency runs before the route-level
    # require_write one, so no token at all is a 401, not a 403
    assert c.put("/config/models", json=body).status_code == 401  # no token
    r = c.put("/config/models", json=body, headers={"authorization": "Bearer ro"})
    assert r.status_code == 403  # read-only token passes require_read but not require_write
    r = c.put("/config/models", json=body, headers={"authorization": "Bearer full"})
    assert r.status_code == 200, r.text
    # a read-only token can still read the saved result
    r = c.get("/config/models", headers={"authorization": "Bearer ro"})
    assert r.json()["roles"]["hunter"]["model"] == "deepseek/deepseek-r1-0528"
