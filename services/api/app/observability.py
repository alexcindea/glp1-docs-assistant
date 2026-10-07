"""Structured logs (structlog) and traces (OpenTelemetry, viewed in Phoenix).

Logs: our code and library loggers (uvicorn, alembic) share one renderer:
readable console output in development, JSON in containers. Every line logged
inside a request carries its trace_id, so it can be found in Phoenix.

Traces: FastAPI and SQLAlchemy spans are exported over OTLP/HTTP when
OTEL_TRACES_ENDPOINT is set (Phoenix: http://127.0.0.1:6006/v1/traces).
Unset means no tracing, which is what tests and CI use.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings

# Phoenix groups traces into projects by this resource attribute.
_PHOENIX_PROJECT_ATTRIBUTE = "openinference.project.name"


def add_trace_ids(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Route structlog and stdlib logging through one handler. Call once per process."""
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_ids,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info
            if settings.log_format == "json"
            else structlog.processors.StackInfoRenderer(),
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # Uvicorn installs its own handlers; send its records through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        library_logger = logging.getLogger(name)
        library_logger.handlers = []
        library_logger.propagate = True
    # The readiness check reads the schema revision through Alembic, which logs
    # two INFO lines per call. The alembic CLI configures its own logging.
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)


def create_tracer_provider(settings: Settings) -> TracerProvider | None:
    """An OTLP-exporting provider, or None when tracing is not configured."""
    if not settings.otel_traces_endpoint:
        return None
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            _PHOENIX_PROJECT_ATTRIBUTE: settings.otel_project_name,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_traces_endpoint))
    )
    return provider


def instrument_app(app: FastAPI, provider: TracerProvider) -> None:
    """Trace every request through `app`."""
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)


def instrument_engine(engine: AsyncEngine, provider: TracerProvider) -> None:
    """Trace every query on `engine`.

    The instrumentor is a process-wide singleton: a second call while one engine
    is instrumented does nothing. Call uninstrument_engines() on shutdown so the
    next app in the same process (tests) can instrument its own engine.
    """
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)


def uninstrument_engines() -> None:
    SQLAlchemyInstrumentor().uninstrument()
