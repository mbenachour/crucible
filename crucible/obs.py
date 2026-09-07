"""Observability — logging + optional OpenTelemetry tracing (specs.md §13).

Two independent things:

1. **Logging** — always on. Console (INFO) + a per-run file at
   ``<workspace>/run.log`` (DEBUG). `configure_logging()` is called once by the
   CLI; library modules just use ``logging.getLogger(__name__)``.

2. **Tracing** — opt-in, **local-only by default**. Enabled when
   ``CRUCIBLE_OTEL`` is truthy or ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. Spans
   go to your OTLP collector (Jaeger / Tempo / SigNoz / OpenObserve / Langfuse),
   never to LangChain's cloud — we force ``LANGSMITH_OTEL_ONLY=true``. For a
   data-residency product, customer-code-derived traces must not leave the box.

`span()` is a no-op context manager when tracing is disabled, so nodes can wrap
work unconditionally.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

LOGGER_NAME = "crucible"
_TRACING_ENABLED = False


# --------------------------------------------------------------------- logging

def configure_logging(workspace_path: str | os.PathLike | None = None, *, level: str = "") -> logging.Logger:
    """Console handler + (if a workspace is given) a DEBUG file handler."""
    lvl = getattr(logging, (level or os.environ.get("CRUCIBLE_LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    console = logging.StreamHandler()
    console.setLevel(lvl)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s  %(message)s", "%H:%M:%S"))
    logger.addHandler(console)

    if workspace_path:
        log_path = Path(workspace_path) / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s:%(lineno)d  %(message)s")
        )
        logger.addHandler(fh)

    return logger


# --------------------------------------------------------------------- tracing

def _tracing_requested() -> bool:
    return bool(
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("CRUCIBLE_OTEL", "").lower() in ("1", "true", "yes", "on")
    )


def setup_tracing() -> bool:
    """Wire LangChain/LangGraph → OTLP if requested. Returns whether it engaged."""
    global _TRACING_ENABLED
    log = logging.getLogger(LOGGER_NAME)
    if not _tracing_requested():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "tracing requested but OpenTelemetry is not installed — run "
            "`pip install \"crucible[otel]\"`. Continuing without traces."
        )
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": "crucible"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Make LangChain emit OTel spans to our provider, and ONLY there.
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
    os.environ.setdefault("LANGSMITH_OTEL_ONLY", "true")

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "(OTLP default :4318)")
    log.info("tracing enabled — exporting spans to %s (local-only)", endpoint)
    _TRACING_ENABLED = True
    return True


@contextlib.contextmanager
def span(name: str, **attributes):
    """Start an OTel span if tracing is on; otherwise a no-op."""
    if not _TRACING_ENABLED:
        yield None
        return
    from opentelemetry import trace

    tracer = trace.get_tracer("crucible")
    with tracer.start_as_current_span(name) as sp:
        for k, v in attributes.items():
            try:
                sp.set_attribute(k, v)
            except Exception:  # noqa: BLE001 — attributes must never break the run
                pass
        yield sp
