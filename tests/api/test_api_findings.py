"""Findings & validations endpoints (issue #41)."""

from __future__ import annotations

from tests.api.conftest import RUN_A, SHARED_KEY


def test_list_findings_default_order_is_severity(client):
    r = client.get(f"/runs/{RUN_A}/findings")
    assert r.status_code == 200
    items = r.json()["items"]
    assert r.json()["total"] == 5
    sev = [f["severity"] for f in items]
    assert sev[0] == "critical" and sev[-1] == "low"
    # list omits the validation trail
    assert items[0]["validation_trail"] is None


def test_filter_by_status_and_severity(client):
    r = client.get(f"/runs/{RUN_A}/findings", params={"status": "reach_upheld"})
    assert [f["finding_id"] for f in r.json()["items"]] == ["A-crit"]

    r = client.get(f"/runs/{RUN_A}/findings", params={"severity": ["high", "critical"]})
    got = {f["finding_id"] for f in r.json()["items"]}
    assert got == {"A-crit", "A-high", "A-dupe"}

    r = client.get(f"/runs/{RUN_A}/findings", params={"min_severity": "high"})
    assert {f["severity"] for f in r.json()["items"]} <= {"high", "critical"}


def test_filter_by_attack_class(client):
    r = client.get(f"/runs/{RUN_A}/findings", params={"attack_class": "template_injection"})
    assert {f["finding_id"] for f in r.json()["items"]} == {"A-high", "A-dupe"}


def test_bad_status_is_422(client):
    r = client.get(f"/runs/{RUN_A}/findings", params={"status": "banana"})
    assert r.status_code == 422
    assert r.json()["error"] == "bad status filter"


def test_upheld_shorthand(client):
    r = client.get(f"/runs/{RUN_A}/findings/upheld")
    assert [f["finding_id"] for f in r.json()["items"]] == ["A-crit"]


def test_finding_detail_has_trail_and_provenance(client):
    r = client.get("/findings/A-crit")
    assert r.status_code == 200
    j = r.json()
    assert j["threat_model"]["attacker"].startswith("unauthenticated")
    assert j["provenance"]["hunter_model"] == "deepseek/deepseek-chat"
    passes = [v["pass_name"] for v in j["validation_trail"]]
    assert passes == ["mechanical", "bug", "reachability"]

    assert client.get("/findings/nope").status_code == 404


def test_duplicate_exposes_duplicate_of(client):
    j = client.get("/findings/A-dupe").json()
    assert j["status"] == "duplicate"
    assert j["duplicate_of"] == "A-high"


def test_validations_endpoint(client):
    r = client.get("/findings/A-crit/validations")
    assert r.status_code == 200
    assert [v["verdict"] for v in r.json()] == ["mechanical_passed", "upheld", "upheld"]


def test_cross_run_stable_key(client):
    r = client.get("/findings", params={"stable_key": SHARED_KEY})
    assert r.status_code == 200
    got = {f["finding_id"] for f in r.json()["items"]}
    assert got == {"A-crit", "B-crit"}
    # newest first (created_at desc)
    assert r.json()["items"][0]["created_at"] >= r.json()["items"][1]["created_at"]
