"""Runs / reports / metrics / coverage endpoints (issue #40)."""

from __future__ import annotations

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
