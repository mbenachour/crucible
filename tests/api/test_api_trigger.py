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


def test_trigger_reaps_a_stale_plain_cli_run_before_counting(trigger_client, store):
    """The bug this guards against: a plain `crucible run` (no pid recorded —
    the historical case, and still true for anything not launched via the
    API) sitting at status=running forever permanently ate a concurrency slot."""
    from datetime import UTC, datetime, timedelta

    from crucible.store.models import Run

    store.create_run("old_cli_run", "/repo", "abc123", "python")
    with store.session() as s:
        s.get(Run, "old_cli_run").created_at = datetime.now(UTC) - timedelta(hours=48)

    with patch("crucible.api.routers.trigger.launch_run", return_value="fresh") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World"})
    assert r.status_code == 202
    launch.assert_called_once()
    assert store.get_run("old_cli_run").outcome == "stale"
    assert store.get_run("old_cli_run").finished_at is not None


def test_get_run_exposes_launch_fields(trigger_client, store):
    store.create_launch("launching1", "octocat/Hello-World")
    r = trigger_client.get("/runs/launching1")
    assert r.status_code == 200
    j = r.json()
    assert j["source_spec"] == "octocat/Hello-World"
    assert j["clone_status"] == "pending"
    assert j["clone_error"] == ""


def test_run_without_override_has_empty_model_override(trigger_client, store):
    store.create_launch("plain1", "octocat/Hello-World")
    r = trigger_client.get("/runs/plain1")
    assert r.status_code == 200
    assert r.json()["model_override"] == {}


# --- per-run model overrides (issue #77) ------------------------------------

def test_model_override_accepted_and_passed_to_launch_run(trigger_client):
    body = {
        "repo": "octocat/Hello-World",
        "models": {"hunter": {"model": "deepseek/deepseek-r1-0528"}},
    }
    with patch("crucible.api.routers.trigger.launch_run", return_value="ov1") as launch:
        r = trigger_client.post("/runs", json=body)
    assert r.status_code == 202, r.text
    launch.assert_called_once()
    assert launch.call_args.kwargs["model_override"] == {
        "hunter": {"model": "deepseek/deepseek-r1-0528"}
    }


def test_model_override_persisted_and_shown_in_run_detail(trigger_client, store):
    # store.create_launch is the persistence path launch_run itself calls
    # synchronously (before the background clone/spawn thread) — exercised
    # directly here to keep this a run-detail-shape test, not a launcher test.
    store.create_launch(
        "ov2", "octocat/Hello-World",
        model_override={"hunter": {"model": "deepseek-chat"}},
    )
    detail = trigger_client.get("/runs/ov2").json()
    assert detail["model_override"] == {"hunter": {"model": "deepseek-chat"}}


def test_model_override_unknown_role_is_422_without_launching(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"not_a_role": {"model": "x"}},
        })
    assert r.status_code == 422
    assert "unknown model role" in r.json()["detail"]
    launch.assert_not_called()


def test_model_override_unresolvable_model_is_422(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"recon": {"model": "not-a-catalog-model"}},
        })
    assert r.status_code == 422
    assert "catalog" in r.json()["detail"]
    launch.assert_not_called()


def test_model_override_rejects_provider_field(trigger_client):
    """Issue #80: no provider choice — every role is OpenRouter."""
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"recon": {"provider": "openrouter"}},
        })
    assert r.status_code == 422
    assert "unexpected field" in r.json()["detail"]
    launch.assert_not_called()


def test_model_override_hunter_eq_validator_via_override_is_422(trigger_client):
    """Host defaults already differ (deepseek vs qwen); the override alone
    creates the collision — must still be rejected."""
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"validator_bug": {"model": "deepseek/deepseek-chat-v3.1"}},
        })
    assert r.status_code == 422
    assert "different models" in r.json()["detail"]
    launch.assert_not_called()


def test_model_override_hunter_eq_validator_via_override_on_hunter_is_422(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"hunter": {"model": "qwen/qwen3-32b"}},
        })
    assert r.status_code == 422
    assert "different models" in r.json()["detail"]
    launch.assert_not_called()


def test_model_override_rejects_api_key_field(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run") as launch:
        r = trigger_client.post("/runs", json={
            "repo": "octocat/Hello-World",
            "models": {"hunter": {"api_key": "sk-should-never-be-accepted"}},
        })
    assert r.status_code == 422
    assert "sk-should-never-be-accepted" not in r.text
    launch.assert_not_called()


def test_no_models_key_behaves_exactly_like_before(trigger_client):
    with patch("crucible.api.routers.trigger.launch_run", return_value="plain2") as launch:
        r = trigger_client.post("/runs", json={"repo": "octocat/Hello-World"})
    assert r.status_code == 202
    assert launch.call_args.kwargs["model_override"] is None
