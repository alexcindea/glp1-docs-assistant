"""Requests produce spans, and log lines carry the trace they belong to."""

from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Settings
from app.factory import create_app
from app.observability import add_trace_ids

UNREACHABLE_URL = "postgresql+asyncpg://app:app@127.0.0.1:1/app"


def test_each_request_produces_a_server_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(Settings(database_url=UNREACHABLE_URL), tracer_provider=provider)

    with TestClient(app) as client:
        client.get("/health")

    server_spans = [s for s in exporter.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert [s.name for s in server_spans] == ["GET /health"]


def test_database_queries_appear_as_spans_inside_the_request(
    migrated_database_url: str,
) -> None:
    """Guards the SQLAlchemy <2.1 pin: the instrumentor silently does nothing on 2.1."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(Settings(database_url=migrated_database_url), tracer_provider=provider)

    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200

    spans = exporter.get_finished_spans()
    (server,) = [s for s in spans if s.kind == trace.SpanKind.SERVER]
    statements = {s: str((s.attributes or {}).get("db.statement", "")) for s in spans}
    queries = [s for s, statement in statements.items() if statement]
    assert any("pg_extension" in statements[s] for s in queries)
    assert all(s.context.trace_id == server.context.trace_id for s in queries)


def test_log_events_inside_a_span_get_its_trace_id() -> None:
    tracer = TracerProvider().get_tracer(__name__)

    with tracer.start_as_current_span("work") as span:
        event = add_trace_ids(None, "info", {"event": "hello"})
    outside = add_trace_ids(None, "info", {"event": "hello"})

    assert event["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert "trace_id" not in outside
