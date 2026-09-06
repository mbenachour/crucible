"""ToolGateMiddleware — hard per-role tool allow-list (specs.md §1.6).

"Validators cannot file findings." A validator agent simply must not have a
`file_finding` / write tool in reach. Rather than build a different tool set per
call site and hope, this middleware refuses — at `wrap_tool_call` time — any
tool whose name is not in the allow-list, returning an error `ToolMessage` the
model can see and react to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class ToolGateMiddleware(AgentMiddleware):
    def __init__(self, allowed: Iterable[str]) -> None:
        super().__init__()
        self.allowed: frozenset[str] = frozenset(allowed)

    @property
    def name(self) -> str:  # stable id in traces
        return "ToolGateMiddleware"

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        call = request.tool_call
        name = call.get("name", "")
        if name not in self.allowed:
            return ToolMessage(
                content=(
                    f"Tool {name!r} is not permitted for this agent role. "
                    f"Permitted tools: {sorted(self.allowed)}."
                ),
                tool_call_id=call.get("id", ""),
                name=name,
                status="error",
            )
        return handler(request)
