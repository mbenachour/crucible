"""Live log tailing — no web-framework dependency, so it's testable with plain
asyncio instead of through an HTTP client's stream-buffering quirks.

Used by `GET /runs/{id}/log/stream` (`routers/artifacts.py`), which supplies
the FastAPI-specific bits (`is_finished` reads the store, `is_disconnected` is
`Request.is_disconnected`) as plain callables.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

DEFAULT_POLL_S = 0.5
DEFAULT_MAX_S = 4 * 3600  # give up after this long regardless — a client just reconnects
DEFAULT_IDLE_POLLS_AFTER_FINISH = 2  # grace polls once finished, to flush a trailing write


async def tail_file(
    path: Path,
    *,
    tail_lines: int = 200,
    poll_s: float = DEFAULT_POLL_S,
    max_s: float = DEFAULT_MAX_S,
    idle_polls_after_finish: int = DEFAULT_IDLE_POLLS_AFTER_FINISH,
    is_finished: Callable[[], bool] | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Yield `path`'s last `tail_lines` lines, then keep yielding newly
    appended content until `is_finished()` has been true for
    `idle_polls_after_finish` consecutive polls with nothing new, `max_s`
    elapses, `is_disconnected()` says the client is gone, or the file
    disappears out from under us.
    """
    with path.open("r", errors="replace") as f:
        text = f.read()
        pos = f.tell()
    if tail_lines:
        lines = text.splitlines()[-tail_lines:]
        if lines:
            yield "\n".join(lines) + "\n"

    elapsed = 0.0
    idle_since_finished = 0
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < pos:
            pos = 0  # rotated/truncated under us — start over rather than error
        if size > pos:
            with path.open("r", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            if chunk:
                yield chunk if chunk.endswith("\n") else chunk + "\n"
                idle_since_finished = 0

        if is_disconnected is not None and await is_disconnected():
            return

        if is_finished is not None and is_finished() and size <= pos:
            idle_since_finished += 1
            if idle_since_finished >= idle_polls_after_finish:
                return

        elapsed += poll_s
        if elapsed >= max_s:
            return
        await asyncio.sleep(poll_s)
