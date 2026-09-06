"""Agent factory — one `create_agent` + middleware stack per stage
(specs.md §0, docs/langchain-harness-notes.md §4).

The stage machine (`crucible/graph/`) stays a LangGraph `StateGraph` for
ordering, fan-out, and checkpoint/resume. Each *stage* builds its agent here.

Middleware order (outermost first — before-hooks run in list order, wrap-hooks
nest, after-hooks reverse):

  1. ToolGateMiddleware        (if `allowed_tools` given)  — refuse disallowed tools
  2. InstrumentationMiddleware — count every tool call, flush on finish
  3. ClassificationMiddleware  — classify response text; retry / refuse / repair
  4. ContextEditingMiddleware  — offload large tool outputs (head+tail kept)
  5. SummarizationMiddleware   — compact history near the context ceiling
  6. ModelCallLimitMiddleware  — hard cap on model calls per run (pairs with §8)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ContextEditingMiddleware,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
)

from crucible.agents.middleware import (
    ClassificationMiddleware,
    InstrumentationMiddleware,
    ToolGateMiddleware,
)
from crucible.config import MODEL_CALLS_PER_TASK, SUMMARIZE_AT_FRACTION
from crucible.llm.registry import ModelRegistry, ModelRole


def build_agent(
    *,
    role: ModelRole,
    registry: ModelRegistry,
    run_id: str,
    tools: Sequence[Any],
    system_prompt: str,
    allowed_tools: Iterable[str] | None = None,
    store: Any | None = None,
    response_format: Any | None = None,
    summarize: bool = True,
    model_call_limit: int = MODEL_CALLS_PER_TASK,
):
    """Return a compiled agent graph for one stage.

    `allowed_tools` — if given, a hard allow-list (see `ToolGateMiddleware`);
    validators pass a read-only set so they cannot file findings (§1.6).
    `response_format` — a Pydantic model for guided structured output (Hunt).
    """
    model = registry.chat_model(role)

    middleware: list[Any] = []
    if allowed_tools is not None:
        middleware.append(ToolGateMiddleware(allowed_tools))
    middleware.append(InstrumentationMiddleware(run_id=run_id, role=role.value, store=store))
    middleware.append(ClassificationMiddleware())
    middleware.append(ContextEditingMiddleware())
    if summarize:
        middleware.append(
            SummarizationMiddleware(model=model, trigger=("fraction", SUMMARIZE_AT_FRACTION))
        )
    middleware.append(
        ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="end")
    )

    return create_agent(
        model,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=middleware,
        response_format=response_format,
    )
