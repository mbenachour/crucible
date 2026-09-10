"""Artifact index + raw fetch + typed shortcuts (issue #42)."""

from __future__ import annotations

import pytest

from crucible.api.artifacts import (
    ArtifactForbidden,
    ArtifactNotFound,
    artifact_index,
    resolve_artifact,
)
from tests.api.conftest import RUN_A


def test_index_tags_every_file(client):
    r = client.get(f"/runs/{RUN_A}/artifacts")
    assert r.status_code == 200
    kinds = {m["path"]: m["kind"] for m in r.json()}
    assert kinds["architecture.md"] == "architecture"
    assert kinds["report.json"] == "report"
    assert kinds["recon/seed.json"] == "recon"
    assert kinds["coverage/api.md"] == "coverage"
    assert kinds["dedup/clusters.json"] == "dedup"
    assert kinds["findings/A-crit.json"] == "finding"
    assert kinds["offload/call-1.txt"] == "offload"
    assert kinds["run.log"] == "log"


def test_raw_fetch_content_type(client):
    r = client.get(f"/runs/{RUN_A}/artifacts/architecture.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    r = client.get(f"/runs/{RUN_A}/artifacts/recon/seed.json")
    assert r.json()["repo_kind"] == "web_api"


def test_traversal_blocked(client):
    for bad in ("../secret", "/etc/passwd", "..%2f..%2fx", ".git/config"):
        r = client.get(f"/runs/{RUN_A}/artifacts/{bad}")
        assert r.status_code in (403, 404), bad


def test_missing_is_404(client):
    r = client.get(f"/runs/{RUN_A}/artifacts/nope.json")
    assert r.status_code == 404
    assert r.json()["error"] == "artifact not found"


def test_typed_shortcuts(client):
    assert client.get(f"/runs/{RUN_A}/architecture").text.startswith("# Architecture")
    assert client.get(f"/runs/{RUN_A}/recon/threat-model").json()["attackers"] == ["remote"]
    assert client.get(f"/runs/{RUN_A}/recon/attack-surface").json()[0]["score"] == 15
    assert client.get(f"/runs/{RUN_A}/dedup/clusters").json()["clusters"] == [["A-high", "A-dupe"]]
    r = client.get(f"/runs/{RUN_A}/recon/bogus")
    assert r.status_code == 404


def test_log_tail(client):
    r = client.get(f"/runs/{RUN_A}/log", params={"tail": 2})
    assert r.text.strip().splitlines() == ["line4", "line5"]


def test_resolver_unit(tmp_path):
    ws = tmp_path / "w"
    (ws / "recon").mkdir(parents=True)
    (ws / "architecture.md").write_text("x")
    (ws / "recon" / "seed.json").write_text("{}")
    assert resolve_artifact(ws, "architecture.md").name == "architecture.md"
    with pytest.raises(ArtifactForbidden):
        resolve_artifact(ws, "../escape")
    with pytest.raises(ArtifactForbidden):
        resolve_artifact(ws, "sub/../../escape")
    with pytest.raises((ArtifactForbidden, ArtifactNotFound)):
        resolve_artifact(ws, "/etc/passwd")
    with pytest.raises(ArtifactNotFound):
        resolve_artifact(ws, "recon/missing.json")
    idx = {m.path for m in artifact_index(ws)}
    assert idx == {"architecture.md", "recon/seed.json"}
