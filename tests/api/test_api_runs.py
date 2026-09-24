"""Runs / reports / metrics / coverage endpoints (issue #40)."""

from __future__ import annotations

from unittest.mock import patch

from tests.api.conftest import RUN_A, RUN_B


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["store_ok"] is True


def test_list_runs_and_filters(client):
    r = client.get("/runs")
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 2
    ids = {run["run_id"] for run in body["items"]}
    assert ids == {RUN_A, RUN_B}

    assert client.get("/runs", params={"language": "python"}).json()["total"] == 1
    assert client.get("/runs", params={"outcome": "completed"}).json()["total"] == 1
    assert client.get("/runs", params={"repo": "other"}).json()["total"] == 1


def test_get_run_has_counts(client):
    r = client.get(f"/runs/{RUN_A}")
    assert r.status_code == 200
    j = r.json()
    assert j["counts"]["total"] == 5
    assert j["counts"]["reach_upheld"] == 1
    assert j["outcome"] == "completed"
    assert j["report_available"] is True

    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope").json()["error"] == "run not found"


def test_report_json_and_md(client):
    r = client.get(f"/runs/{RUN_A}/report")
    assert r.status_code == 200
    assert r.json()["counts"]["upheld"] == 1

    r = client.get(f"/runs/{RUN_A}/report.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "Security report" in r.text


def test_report_absent_is_404(client):
    r = client.get(f"/runs/{RUN_B}/report")
    assert r.status_code == 404
    assert r.json()["error"] == "report not available"


def test_metrics(client):
    r = client.get(f"/runs/{RUN_A}/metrics")
    assert r.status_code == 200
    j = r.json()
    assert j["fork_rate"] == "3/12"
    assert j["tool_usage"]["sandbox_exec"]["count"] == 12
    assert j["counts"]["total"] == 5
    assert j["token_spend"] == 123456  # pulled from report.json


def test_coverage_matrix_and_buckets(client):
    r = client.get(f"/runs/{RUN_A}/coverage")
    assert r.status_code == 200
    j = r.json()
    assert j["matrix_cells"] == 4
    assert j["covered_cells"] == 2  # sql_injection + template_injection have passes
    by_class = {c["attack_class"]: c for c in j["cells"]}
    assert by_class["sql_injection"]["productive"] is True
    assert by_class["template_injection"]["productive"] is False
    # path_traversal + auth_bypass never hunted -> missing
    assert set(j["gapfill_buckets"]["missing"]) == {"api::path_traversal", "core::auth_bypass"}
    # A-med (path_traversal) failed mechanical for an actionable reason -> failed bucket
    assert "api::path_traversal" in j["gapfill_buckets"]["failed"]


def test_cancel_unknown_run_is_404(client):
    r = client.post("/runs/nope/cancel")
    assert r.status_code == 404


def test_cancel_already_finished_run_is_409(client):
    r = client.post(f"/runs/{RUN_A}/cancel")  # RUN_A is finished (outcome=completed)
    assert r.status_code == 409
    assert "already finished" in r.json()["detail"]


def test_cancel_run_with_no_pid_is_409(client):
    r = client.post(f"/runs/{RUN_B}/cancel")  # RUN_B is unfinished but has no pid
    assert r.status_code == 409
    assert "no pid recorded" in r.json()["detail"]


def test_cancel_live_run_kills_and_marks_cancelled(client, store):
    store.create_launch("live_run_1", "o/r")
    store.set_pid("live_run_1", 4242)

    with patch("crucible.api.launcher.os.kill", side_effect=ProcessLookupError), \
         patch("crucible.api.launcher.os.killpg") as killpg:
        r = client.post("/runs/live_run_1/cancel")

    assert r.status_code == 200
    j = r.json()
    assert j["outcome"] == "cancelled"
    assert j["status"] == "finished"
    killpg.assert_not_called()  # already dead by the time we checked — no signal needed

    # idempotent: cancelling again 409s instead of re-marking it
    r2 = client.post("/runs/live_run_1/cancel")
    assert r2.status_code == 409
