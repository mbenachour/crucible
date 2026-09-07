"""Observability — logging + optional tracing (specs.md §13).

1. **Logging** — always on. Console (INFO) + a per-run file at
   ``<workspace>/run.log`` (DEBUG). `configure_logging()` is called once by the
   CLI; library modules just use ``logging.getLogger(__name__)``.

2. **Tracing** — opt-in, three modes chosen by env (`setup_tracing()`):

   - **LangSmith** — ``LANGSMITH_TRACING=true`` + ``LANGSMITH_API_KEY``.
     LangChain/LangGraph auto-trace to LangSmith; we just default
     ``LANGSMITH_PROJECT``. Per specs §13 this is the *internal-dev* path.
   - **Local OTLP** — ``CRUCIBLE_OTEL`` truthy or ``OTEL_EXPORTER_OTLP_ENDPOINT``
     set (and LangSmith *not* configured). Spans go to your OTLP collector only
     (``LANGSMITH_OTEL_ONLY=true``) — the data-residency path.
   - **Both** — LangSmith configured *and* an OTLP endpoint set: spans fan out to
     both.

`span()` is a no-op context manager unless a local OTLP provider was installed,
so nodes can wrap work unconditionally.
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

def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _langsmith_requested() -> bool:
    return bool(os.environ.get("LANGSMITH_API_KEY")) and (
        _truthy("LANGSMITH_TRACING") or _truthy("LANGCHAIN_TRACING_V2")
    )


def _otel_requested() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")) or _truthy("CRUCIBLE_OTEL")


_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318"


def _otlp_endpoint() -> str:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or _DEFAULT_OTLP_ENDPOINT


def _endpoint_reachable(url: str, timeout: float = 0.6) -> bool:
    """Cheap TCP preflight so we don't wire an exporter that will spam retries
    and hang on shutdown against a dead collector."""
    import socket
    from urllib.parse import urlparse

    p = urlparse(url if "://" in url else f"http://{url}")
    host = p.hostname or "localhost"
    port = p.port or (443 if p.scheme == "https" else 4318)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def setup_tracing() -> str:
    """Wire tracing per env. Returns the mode: '', 'langsmith', 'otel', 'both'."""
    global _TRACING_ENABLED
    log = logging.getLogger(LOGGER_NAME)
    ls = _langsmith_requested()
    otel = _otel_requested()
    if not ls and not otel:
        return ""

    if ls:
        os.environ.setdefault("LANGSMITH_PROJECT", "crucible")
        os.environ["LANGSMITH_TRACING"] = "true"  # normalise
        endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
        log.info(
            "LangSmith tracing enabled — project=%s endpoint=%s",
            os.environ["LANGSMITH_PROJECT"], endpoint,
        )

    if otel:
        endpoint = _otlp_endpoint()
        if not _endpoint_reachable(endpoint):
            log.warning(
                "OTLP collector at %s is unreachable — skipping OTLP tracing "
                "(start a collector or unset CRUCIBLE_OTEL / OTEL_EXPORTER_OTLP_ENDPOINT).",
                endpoint,
            )
            otel = False
    if otel:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            log.warning(
                "OTLP tracing requested but OpenTelemetry is not installed — run "
                "`pip install \"crucible[otel]\"`. Continuing without OTLP."
            )
            otel = False
        else:
            # Quiet the exporter's retry chatter and bound its blocking.
            logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.ERROR)
            os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "3000")
            provider = TracerProvider(resource=Resource.create({"service.name": "crucible"}))
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(timeout=3), export_timeout_millis=3000)
            )
            trace.set_tracer_provider(provider)
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
            if not ls:
                # data-residency path: spans to the local collector ONLY
                os.environ.setdefault("LANGSMITH_OTEL_ONLY", "true")
            log.info(
                "OTLP tracing enabled — spans to %s%s",
                endpoint, "" if ls else " (local-only)",
            )
            _TRACING_ENABLED = True

    return "both" if (ls and otel) else "langsmith" if ls else "otel" if otel else ""


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
