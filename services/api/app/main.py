"""ASGI entry point: `fastapi dev` and uvicorn load `app.main:app`.

Does the once-per-process setup (logging, the global tracer provider), then
builds the app. Tests use app.factory.create_app instead, so importing them has
no side effects.
"""

from opentelemetry import trace

from app.config import get_settings
from app.factory import create_app
from app.observability import configure_logging, create_tracer_provider

settings = get_settings()
configure_logging(settings)
tracer_provider = create_tracer_provider(settings)
if tracer_provider:
    trace.set_tracer_provider(tracer_provider)
app = create_app(settings, tracer_provider=tracer_provider)
