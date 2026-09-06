"""Crucible-specific agent middleware (specs.md §1.6, §1.10, §1.12).

What we do NOT hand-roll — LangChain 1.x ships these and `agents/core.py` wires
them straight in:

- tool-output offloading  -> `ContextEditingMiddleware`
- compaction              -> `SummarizationMiddleware`
- model-call / token cap  -> `ModelCallLimitMiddleware`
- Docker shell sandbox    -> `ShellToolMiddleware` + `DockerExecutionPolicy`

What is specific to this harness and lives here:

- `ToolGateMiddleware`        — hard per-role allow-list; validators cannot reach
  finding-writing tools (§1.6).
- `ClassificationMiddleware`  — classify the response *text* before it is used;
  transient errors inside `200 OK` are retried, refusals fail the task with no
  silent rephrase (§1.10, §6).
- `InstrumentationMiddleware` — per-tool invocation counters flushed to the
  domain store (§1.12).
"""

from crucible.agents.middleware.classification import (
    ClassificationMiddleware,
    MalformedResponseError,
    RefusalError,
    TransientExhaustedError,
)
from crucible.agents.middleware.instrumentation import InstrumentationMiddleware
from crucible.agents.middleware.tool_gate import ToolGateMiddleware

__all__ = [
    "ToolGateMiddleware",
    "ClassificationMiddleware",
    "InstrumentationMiddleware",
    "RefusalError",
    "MalformedResponseError",
    "TransientExhaustedError",
]
