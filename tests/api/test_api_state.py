"""Execution-state endpoint (issue #43).

The happy path needs a real LangGraph checkpoint; that is exercised by the
end-to-end recon run in tests/test_cli.py. Here we cover the contract edges.
"""

from __future__ import annotations

from tests.api.conftest import RUN_A


def test_unknown_run_is_404(client):
    assert client.get("/runs/does-not-exist/state").status_code == 404


def test_no_checkpoint_is_404(client):
    # RUN_A exists in the store but the API's checkpoint_db is empty.
    r = client.get(f"/runs/{RUN_A}/state")
    assert r.status_code == 404
    assert r.json()["error"] == "no checkpoint"
