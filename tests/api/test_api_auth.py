"""Auth + safety (issue #45)."""

from __future__ import annotations

import pytest

from crucible.api.settings import ApiSettings
from tests.api.conftest import RUN_A


@pytest.fixture
def auth_client(store, workspace, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app

    settings = ApiSettings(
        store_url=str(store.engine.url),
        checkpoint_db=str(tmp_path / "c.sqlite"),
        workspace_root=str(tmp_path / "ws_a"),
        auth_token="full-secret",
        auth_token_readonly="ro-secret",
        serve_ui=False,
    )
    app = create_app(settings)
    app.state.store = store
    return TestClient(app, raise_server_exceptions=False)


def test_health_is_open(auth_client):
    assert auth_client.get("/health").status_code == 200


def test_read_requires_a_token(auth_client):
    assert auth_client.get(f"/runs/{RUN_A}").status_code == 401
    ok = auth_client.get(f"/runs/{RUN_A}", headers={"authorization": "Bearer ro-secret"})
    assert ok.status_code == 200
    ok = auth_client.get(f"/runs/{RUN_A}", headers={"authorization": "Bearer full-secret"})
    assert ok.status_code == 200
    assert auth_client.get(f"/runs/{RUN_A}", headers={"authorization": "Bearer nope"}).status_code == 401


def test_write_needs_full_token(auth_client):
    wid = auth_client.get("/wishes", headers={"authorization": "Bearer ro-secret"}).json()["items"][0]["id"]
    r = auth_client.post(f"/wishes/{wid}/resolve", headers={"authorization": "Bearer ro-secret"})
    assert r.status_code == 403
    r = auth_client.post(f"/wishes/{wid}/resolve", headers={"authorization": "Bearer full-secret"})
    assert r.status_code == 200


def test_settings_refuses_public_bind_without_auth():
    with pytest.raises(RuntimeError):
        ApiSettings(host="0.0.0.0").validate()
    ApiSettings(host="0.0.0.0", allow_no_auth=True).validate()  # explicit override is fine
    ApiSettings(host="127.0.0.1").validate()  # loopback is fine
