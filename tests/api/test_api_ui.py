"""The dashboard mount + SPA fallback (issue #49)."""

from __future__ import annotations

import pytest


@pytest.fixture
def ui_client(store, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    ui = tmp_path / "ui_dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>Crucible</title><div id=root></div>")
    (ui / "assets" / "app.js").write_text("console.log('hi')")
    (ui / "favicon.ico").write_text("x")

    settings = ApiSettings(store_url=str(store.engine.url), ui_dir=str(ui), serve_ui=True)
    app = create_app(settings)
    app.state.store = store
    return TestClient(app, raise_server_exceptions=False)


def test_serves_index_at_root(ui_client):
    r = ui_client.get("/")
    assert r.status_code == 200 and "Crucible" in r.text


def test_serves_assets_and_static_files(ui_client):
    assert ui_client.get("/assets/app.js").status_code == 200
    assert ui_client.get("/favicon.ico").status_code == 200


def test_deep_link_falls_back_to_index(ui_client):
    # a browser navigation to a client-only route → the SPA
    r = ui_client.get("/findings/some-id", headers={"accept": "text/html"})
    assert r.status_code == 200 and "id=root" in r.text


def test_api_routes_still_win(ui_client):
    # a real API path is JSON, not the SPA — even from a browser Accept
    assert ui_client.get("/health").json()["status"] == "ok"
    r = ui_client.get("/runs/does-not-exist", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.json()["error"] == "run not found"


def test_no_ui_flag_disables_mount(store):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    app = create_app(ApiSettings(store_url=str(store.engine.url), serve_ui=False))
    app.state.store = store
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/").status_code == 404
