"""Per-tool invocation counters (specs.md §1.12, §12).

Instrument what agents actually reach for. Cloudflare plumbed a static
analyzer through the whole system and their Hunters invoked it zero times in
a month, while the wishlist became the single most-used tool. Measure tool
usage and delete what goes unused.

Counters are domain state -> they live in the SQLite store, keyed by
(run_id, role, tool_name), not in graph state.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from time import perf_counter


class ToolUsage:
    """In-process accumulator, flushed to store.dao at node boundaries."""

    def __init__(self, run_id: str, role: str):
        self.run_id = run_id
        self.role = role
        self.counts: Counter[str] = Counter()
        self.latency_s: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()

    @contextmanager
    def record(self, tool_name: str):
        self.counts[tool_name] += 1
        start = perf_counter()
        try:
            yield
        except Exception:
            self.errors[tool_name] += 1
            raise
        finally:
            self.latency_s[tool_name] += perf_counter() - start

    def rows(self) -> list[dict]:
        return [
            {
                "run_id": self.run_id,
                "role": self.role,
                "tool_name": name,
                "count": self.counts[name],
                "latency_s": round(self.latency_s[name], 3),
                "errors": self.errors[name],
            }
            for name in self.counts
        ]
