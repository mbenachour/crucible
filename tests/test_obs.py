"""Logging + optional tracing (specs.md §13)."""

import logging
import os

import crucible.obs as obs


def test_configure_logging_writes_run_log(tmp_path):
    logger = obs.configure_logging(tmp_path)
    logger.info("hello %s", "world")
    for h in logger.handlers:
        h.flush()
    text = (tmp_path / "run.log").read_text()
    assert "hello world" in text


def _clear_tracing_env(mp):
    for v in (
        "CRUCIBLE_OTEL", "OTEL_EXPORTER_OTLP_ENDPOINT",
        "LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
    ):
        mp.delenv(v, raising=False)


def test_tracing_off_by_default(monkeypatch):
    _clear_tracing_env(monkeypatch)
    assert obs.setup_tracing() == ""
    with obs.span("x", a=1) as s:
        assert s is None  # no-op context manager


def test_langsmith_mode(monkeypatch):
    _clear_tracing_env(monkeypatch)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    assert obs.setup_tracing() == "langsmith"
    assert os.environ["LANGSMITH_PROJECT"] == "crucible"     # defaulted
    assert "LANGSMITH_OTEL_ONLY" not in os.environ           # not the local-only path


def test_langsmith_needs_both_key_and_flag(monkeypatch):
    _clear_tracing_env(monkeypatch)
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")   # flag missing
    assert obs.setup_tracing() == ""


def test_otel_skipped_when_collector_unreachable(monkeypatch):
    _clear_tracing_env(monkeypatch)
    obs._TRACING_ENABLED = False
    monkeypatch.setenv("CRUCIBLE_OTEL", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:59999")  # nothing there
    assert obs.setup_tracing() == ""          # not "otel" — preflight failed
    assert obs._TRACING_ENABLED is False      # no provider wired -> no retry spam / hang


def test_otel_falls_back_to_langsmith_when_collector_unreachable(monkeypatch):
    _clear_tracing_env(monkeypatch)
    obs._TRACING_ENABLED = False
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:59999")
    assert obs.setup_tracing() == "langsmith"
    assert obs._TRACING_ENABLED is False


def test_span_is_noop_when_disabled():
    # regardless of env, if _TRACING_ENABLED wasn't set, span yields None
    obs._TRACING_ENABLED = False
    with obs.span("work", k="v") as s:
        assert s is None


def test_otel_requested_detection(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("CRUCIBLE_OTEL", "1")
    assert obs._otel_requested() is True
    monkeypatch.setenv("CRUCIBLE_OTEL", "false")
    assert obs._otel_requested() is False
