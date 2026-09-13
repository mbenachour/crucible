"""crucible/api/log_tail.py — pure asyncio, no FastAPI/HTTP client involved
(that layer buffers streaming responses eagerly and can't exercise real
interleaving; see tests/api/test_api_log_stream.py for the HTTP-level tests
this deliberately doesn't try to duplicate)."""

from __future__ import annotations

import asyncio

import pytest

from crucible.api.log_tail import tail_file


async def _collect(path, **kw):
    out = ""
    async for chunk in tail_file(path, **kw):
        out += chunk
    return out


@pytest.mark.asyncio
async def test_sends_the_tail_then_stops_when_finished(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("l1\nl2\nl3\nl4\nl5\n")
    out = await _collect(log, tail_lines=2, is_finished=lambda: True, poll_s=0.001, idle_polls_after_finish=2)
    assert out.strip().splitlines() == ["l4", "l5"]


@pytest.mark.asyncio
async def test_tail_lines_zero_sends_no_history(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("l1\nl2\n")
    out = await _collect(log, tail_lines=0, is_finished=lambda: True, poll_s=0.001)
    assert out == ""


@pytest.mark.asyncio
async def test_picks_up_a_line_appended_while_live(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("l1\n")
    finished = False

    async def append_then_finish():
        nonlocal finished
        await asyncio.sleep(0.02)
        log.write_text(log.read_text() + "l2-live\n")
        await asyncio.sleep(0.02)
        finished = True

    asyncio.create_task(append_then_finish())
    out = await _collect(log, tail_lines=0, is_finished=lambda: finished, poll_s=0.005, idle_polls_after_finish=2)
    assert out.strip().splitlines() == ["l2-live"]


@pytest.mark.asyncio
async def test_stops_on_disconnect(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("l1\n")
    calls = 0

    async def disconnected():
        nonlocal calls
        calls += 1
        return calls >= 2  # disconnect on the second poll

    out = await _collect(log, tail_lines=0, is_finished=lambda: False, is_disconnected=disconnected, poll_s=0.001)
    assert out == ""
    assert calls == 2


@pytest.mark.asyncio
async def test_stops_after_max_s_even_if_never_finished(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("l1\n")
    t0 = asyncio.get_event_loop().time()
    await _collect(log, tail_lines=0, is_finished=lambda: False, poll_s=0.01, max_s=0.03)
    assert asyncio.get_event_loop().time() - t0 < 0.5  # ended promptly, not left hanging


@pytest.mark.asyncio
async def test_handles_truncation_without_erroring(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("aaaaaaaaaa\n")
    state = {"n": 0}

    def is_finished():
        state["n"] += 1
        if state["n"] == 2:
            log.write_text("short\n")  # shrinks below the old position
        return state["n"] >= 4

    out = await _collect(log, tail_lines=0, is_finished=is_finished, poll_s=0.001, idle_polls_after_finish=1)
    assert "short" in out
