"""POST /runs — the trigger endpoint (issue #58). `launch_run` is mocked; the
launcher and repo-acquisition modules have their own dedicated test suites."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def trigger_client(store, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    settings = ApiSettings(
        store_url=str(store.engine.url),
        checkpoint_db=str(tmp_path / "c.sqlite"),
        runs_dir=str(tmp_path / "runs"),
        serve_ui=False,
        max_concurrent_runs=2,
    )
    app = create_app(settings)
    app.state.store = store
    return TestClient(app, raise_server_exceptions=False)


def test_trigger_returns_202_with_location(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run", return_value="newrun123") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World"})
    assert r.status_code == 202
    assert r.json() == {"run_id": "newrun123", "clone_status": "pending"}
    assert r.headers["location"] == "/runs/newrun123"
    launch.assert_called_once()
    _, kwargs = launch.call_args
    assert kwargs["source_spec"] == "octocat/Hello-World"
    assert kwargs["ref"] is None


def test_trigger_passes_through_ref(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run", return_value="r2") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World", "ref": "main"})
    assert r.status_code == 202
    assert launch.call_args.kwargs["ref"] == "main"


@pytest.mark.parametrize("body", [
    {"repo": ""},
    {"repo": "-oProxyCommand=x"},
    {"repo": "https://internal.example/repo.git"},   # not on the allowlist
    {"repo": "https://169.254.169.254/repo.git"},     # link-local
    {"repo": "octocat/Hello-World", "ref": "--upload-pack=x"},
])
def test_trigger_rejects_bad_input_without_launching(trigger_client, body):
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json=body)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid repo"
    launch.assert_not_called()


def test_trigger_missing_body_is_422(trigger_client):
    assert trigger_client.post("/runs", json={}).status_code == 422


def test_trigger_requires_write_token_when_auth_enabled(store, tmp_path):
    from fastapi.testclient import TestClient

    from crucible.api.app import create_app
    from crucible.api.settings import ApiSettings

    settings = ApiSettings(
        store_url=str(store.engine.url), checkpoint_db=str(tmp_path / "c.sqlite"),
        runs_dir=str(tmp_path / "runs"), serve_ui=False,
        auth_token="full", auth_token_readonly="ro",
    )
    app = create_app(settings)
    app.state.store = store
    client = TestClient(app, raise_server_exceptions=False)

    with patch("crucible.api.routers.trigger.launch_run", return_value="r") as launch:
        # require_write's current convention (§45): missing or wrong write
        # token both come back 403 (only require_read distinguishes 401).
        assert client.post("/runs", json={"repo": "o/r"}).status_code == 403
        r = client.post("/runs", json={"repo": "o/r"}, headers={"authorization": "Bearer ro"})
        assert r.status_code == 403
        r = client.post("/runs", json={"repo": "o/r"}, headers={"authorization": "Bearer full"})
        assert r.status_code == 202
    launch.assert_called_once()


def test_trigger_enforces_concurrency_cap(trigger_client, store):
    from crucible.store.models import Run

    with store.session() as s:
        s.add(Run(run_id="active1", repo_path="", repo_commit="", primary_language=""))
        s.add(Run(run_id="active2", repo_path="", repo_commit="", primary_language=""))

    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World"})
    assert r.status_code == 429
    assert "active" in r.json()["detail"]
    launch.assert_not_called()


def test_trigger_sweeps_stuck_launches_before_counting(trigger_client, store):
    from datetime import UTC, datetime, timedelta

    store.create_launch("stuck", "o/r")
    with store.session() as s:
        from crucible.store.models import Run

        r = s.get(Run, "stuck")
        r.created_at = datetime.now(UTC) - timedelta(hours=1)

    with patch("crucible.api.routers.trigger.launch_run", return_value="fresh") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World"})
    assert r.status_code == 202
    launch.assert_called_once()
    assert store.get_run("stuck").clone_status == "clone_failed"


def test_get_run_exposes_launch_fields(trigger_client, store):
    store.create_launch("launching1", "octocat/Hello-World")
    r = trigger_client.get("/runs/launching1")
    assert r.status_code == 200
    j = r.json()
    assert j["source_spec"] == "octocat/Hello-World"
    assert j["clone_status"] == "pending"
    assert j["clone_error"] == ""
