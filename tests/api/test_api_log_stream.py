"""GET /runs/{id}/log/stream — HTTP-level contract (issue: live logs).

The actual tailing/live-pickup logic is pure asyncio (crucible/api/log_tail.py)
and unit-tested directly in tests/test_log_tail.py — a sync HTTP TestClient
buffers a streaming response eagerly and can't exercise real interleaving, so
this file sticks to what it can reliably assert: headers, initial content, the
tail_lines param, and 404. Live tailing end to end is also verified manually
against a running `crucible serve` (see the log_tail module docstring / PR).
"""

from __future__ import annotations

import pytest

from tests.api.conftest import RUN_A


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # the real intervals (0.5s poll / 4h max) would make this slow; a finished
    # run only needs a couple of quick polls to close the stream out.
    import crucible.api.log_tail as mod

    monkeypatch.setattr(mod, "DEFAULT_POLL_S", 0.01)
    monkeypatch.setattr(mod, "DEFAULT_MAX_S", 0.3)


def _collect(client, path, **kw):
    with client.stream("GET", path, **kw) as r:
        return r.status_code, "".join(r.iter_text())


def test_streams_the_same_content_as_the_static_log_endpoint(client, store):
    store.finish_run(RUN_A, "completed")
    _, full = _collect(client, f"/runs/{RUN_A}/log")
    status, streamed = _collect(client, f"/runs/{RUN_A}/log/stream")
    assert status == 200
    assert streamed.strip().splitlines() == full.strip().splitlines()


def test_content_type_and_no_cache_headers(client, store):
    store.finish_run(RUN_A, "completed")
    with client.stream("GET", f"/runs/{RUN_A}/log/stream") as r:
        assert r.headers["content-type"].startswith("text/plain")
        assert r.headers["cache-control"] == "no-cache"


def test_tail_lines_limits_initial_history(client, store):
    store.finish_run(RUN_A, "completed")
    _, streamed = _collect(client, f"/runs/{RUN_A}/log/stream", params={"tail_lines": 2})
    assert streamed.strip().splitlines() == ["line4", "line5"]


def test_tail_lines_zero_sends_no_history(client, store):
    store.finish_run(RUN_A, "completed")
    _, streamed = _collect(client, f"/runs/{RUN_A}/log/stream", params={"tail_lines": 0})
    assert streamed == ""


def test_missing_log_is_404(client, store, tmp_path):
    other = "no-log-run"
    (tmp_path / "empty_ws").mkdir()
    store.create_run(other, "/x", "c", "py", str(tmp_path / "empty_ws"))
    r = client.get(f"/runs/{other}/log/stream")
    assert r.status_code == 404
