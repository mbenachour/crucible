"""ToolGate + Classification middleware behavior (specs.md §1.6, §1.10)."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from crucible.agents.middleware import (
    ClassificationMiddleware,
    InstrumentationMiddleware,
    RefusalError,
    ToolGateMiddleware,
)
from crucible.agents.middleware.classification import TransientExhaustedError


def _tool_req(name):
    return SimpleNamespace(tool_call={"name": name, "id": "c1", "args": {}})


def _resp(text="", tool_calls=None):
    msg = AIMessage(content=text)
    if tool_calls:
        msg.tool_calls = tool_calls
    return SimpleNamespace(result=[msg])


# --- ToolGateMiddleware --------------------------------------------------

def test_tool_gate_allows_listed_tool():
    mw = ToolGateMiddleware({"read", "grep"})
    out = mw.wrap_tool_call(_tool_req("read"), lambda r: ToolMessage(content="ok", tool_call_id="c1"))
    assert isinstance(out, ToolMessage) and out.content == "ok"


def test_tool_gate_blocks_unlisted_tool_without_calling_handler():
    mw = ToolGateMiddleware({"read"})
    called = False

    def handler(_r):
        nonlocal called
        called = True
        return ToolMessage(content="ran", tool_call_id="c1")

    out = mw.wrap_tool_call(_tool_req("file_finding"), handler)
    assert called is False
    assert isinstance(out, ToolMessage) and out.status == "error"
    assert "not permitted" in out.content


# --- ClassificationMiddleware -----------------------------------------

def test_classification_passes_ok_text_through():
    mw = ClassificationMiddleware()
    r = _resp("here is a normal answer with enough words to look fine")
    assert mw.wrap_model_call(SimpleNamespace(), lambda _q: r) is r


def test_classification_passes_tool_call_turn_through():
    mw = ClassificationMiddleware()
    r = _resp("", tool_calls=[{"name": "read", "args": {}, "id": "c1"}])
    assert mw.wrap_model_call(SimpleNamespace(), lambda _q: r) is r


def test_classification_raises_on_refusal_no_retry():
    mw = ClassificationMiddleware()
    calls = 0

    def handler(_q):
        nonlocal calls
        calls += 1
        return _resp("I can't help with that request.")

    with pytest.raises(RefusalError):
        mw.wrap_model_call(SimpleNamespace(), handler)
    assert calls == 1  # no silent retry-with-rephrasing


def test_classification_retries_transient_then_gives_up():
    mw = ClassificationMiddleware(max_transient=2, base_backoff_s=0)
    calls = 0

    def handler(_q):
        nonlocal calls
        calls += 1
        return _resp("upstream error: gateway timeout")

    with pytest.raises(TransientExhaustedError):
        mw.wrap_model_call(SimpleNamespace(), handler)
    assert calls == 3  # initial + 2 retries


# --- InstrumentationMiddleware --------------------------------------

def test_instrumentation_counts_tool_calls():
    mw = InstrumentationMiddleware(run_id="r", role="hunter", store=None)
    mw.wrap_tool_call(_tool_req("grep"), lambda r: ToolMessage(content="x", tool_call_id="c1"))
    mw.wrap_tool_call(_tool_req("grep"), lambda r: ToolMessage(content="x", tool_call_id="c1"))
    rows = {row["tool_name"]: row["count"] for row in mw.usage.rows()}
    assert rows == {"grep": 2}
