"""ClassificationMiddleware — classify the response text before it is used
(specs.md §6, §1.10).

Transient errors arrive inside `200 OK`. An unclassified response is a failed
task, not a clean one. This wraps every model call:

- `OK`               -> return the response
- `TRANSIENT_ERROR`  -> retry with capped backoff
- `REFUSAL`          -> raise `RefusalError`. **No silent retry-with-rephrasing.**
- `MALFORMED`        -> one repair attempt (append a hint), then raise

`REFUSAL` count is a product metric — a caller may catch `RefusalError` and
record it before failing the task.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage

from crucible.llm.classify import MAX_TRANSIENT_RETRIES, ResponseClass, classify


class RefusalError(RuntimeError):
    """Model declined. Fail the task; do not rephrase and retry."""


class MalformedResponseError(RuntimeError):
    """Unparseable after one repair attempt."""


class TransientExhaustedError(RuntimeError):
    """Retries exhausted on transient errors."""


def _response_text(resp: ModelResponse) -> str:
    parts: list[str] = []
    for msg in getattr(resp, "result", None) or []:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):  # content blocks
            parts.extend(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
            )
    return "\n".join(p for p in parts if p)


class ClassificationMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        max_transient: int = MAX_TRANSIENT_RETRIES,
        base_backoff_s: float = 1.0,
        expect_json: bool = False,
    ) -> None:
        super().__init__()
        self.max_transient = max_transient
        self.base_backoff_s = base_backoff_s
        # Agent turns are often tool calls, not JSON payloads; only the guided-JSON
        # emit step wants expect_json=True. Default off so tool-call turns don't
        # classify as MALFORMED.
        self.expect_json = expect_json

    @property
    def name(self) -> str:
        return "ClassificationMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        repaired = False
        transient_attempts = 0
        while True:
            resp = handler(request)
            text = _response_text(resp)

            # An empty-but-has-tool-calls response is a normal agent turn.
            if not text and _has_tool_calls(resp):
                return resp

            cls = classify(text, expect_json=self.expect_json)
            if cls is ResponseClass.OK:
                return resp
            if cls is ResponseClass.TRANSIENT_ERROR:
                if transient_attempts >= self.max_transient:
                    raise TransientExhaustedError(text[:200] or "empty response")
                time.sleep(self.base_backoff_s * (2**transient_attempts))
                transient_attempts += 1
                continue
            if cls is ResponseClass.REFUSAL:
                raise RefusalError(text[:200])
            if cls is ResponseClass.MALFORMED:
                if repaired:
                    raise MalformedResponseError(text[:200])
                request = request.override(
                    messages=[
                        *request.messages,
                        HumanMessage(
                            "Your previous reply was not valid. Reply again with only the "
                            "requested output and nothing else."
                        ),
                    ]
                )
                repaired = True
                continue
            return resp  # unreachable, keeps type-checkers happy


def _has_tool_calls(resp: ModelResponse) -> bool:
    for msg in getattr(resp, "result", None) or []:
        if getattr(msg, "tool_calls", None):
            return True
    return False
