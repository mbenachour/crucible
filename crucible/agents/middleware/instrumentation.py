"""InstrumentationMiddleware — per-tool invocation counters (specs.md §1.12).

*Instrument what agents actually reach for.* Counts every tool call (and its
latency and error count) via `ToolUsage`, and flushes the rows to the domain
store when the agent finishes. Counters are domain state, keyed by
`(run_id, role, tool_name)` — not graph state.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from crucible.agents.instrumentation import ToolUsage


class InstrumentationMiddleware(AgentMiddleware):
    def __init__(self, *, run_id: str, role: str, store: Any | None = None) -> None:
        super().__init__()
        self.usage = ToolUsage(run_id, role)
        self._store = store

    @property
    def name(self) -> str:
        return "InstrumentationMiddleware"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        name = request.tool_call.get("name", "unknown")
        with self.usage.record(name):
            return handler(request)

    def after_agent(self, state: Any, runtime: Any) -> None:
        if self._store is not None:
            self._store.flush_tool_usage(self.usage.rows())
        return None
