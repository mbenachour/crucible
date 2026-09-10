"""Wishlist endpoints (issue #44)."""

from __future__ import annotations

from tests.api.conftest import RUN_A, RUN_B


def test_per_run_wishes_and_status_filter(client):
    r = client.get(f"/runs/{RUN_A}/wishes")
    assert r.json()["total"] == 2
    r = client.get(f"/runs/{RUN_A}/wishes", params={"status": "open"})
    assert [w["blocked_task_id"] for w in r.json()["items"]] == ["h0007"]


def test_cross_run_defaults_to_open(client):
    r = client.get("/wishes")
    got = {(w["run_id"], w["blocked_task_id"]) for w in r.json()["items"]}
    assert got == {(RUN_A, "h0007"), (RUN_B, "h0002")}


def test_get_and_resolve(client):
    wid = client.get("/wishes").json()["items"][0]["id"]
    assert client.get(f"/wishes/{wid}").json()["status"] == "open"

    r = client.post(f"/wishes/{wid}/resolve", json={"note": "provisioned"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    # idempotent
    assert client.post(f"/wishes/{wid}/resolve").status_code == 200
    assert client.post("/wishes/999999/resolve").status_code == 404
