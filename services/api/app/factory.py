"""FastAPI application factory. Importing this module has no side effects."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from app import health
from app.config import Settings, get_settings
from app.db import create_engine, migration_head
from app.errors import ERROR_RESPONSES, register_error_handlers
from app.observability import instrument_app, instrument_engine, uninstrument_engines
from app.providers import build_embedder, build_llm_provider
from app.state import AppState


def create_app(
    settings: Settings | None = None, tracer_provider: TracerProvider | None = None
) -> FastAPI:
    """Build the app. Requests and queries are traced only when a provider is given.

    Process-wide setup (logging, the global tracer provider) belongs to the entry
    point, app/main.py, not here.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        if tracer_provider:
            instrument_engine(engine, tracer_provider)
        app.state.resources = AppState(
            engine=engine,
            expected_revision=migration_head(),
            llm=build_llm_provider(settings),
            embedder=build_embedder(settings),
        )
        try:
            yield
        finally:
            if tracer_provider:
                uninstrument_engines()
                tracer_provider.shutdown()
            await engine.dispose()

    app = FastAPI(
        title="Docs Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        responses=ERROR_RESPONSES,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    if tracer_provider:
        instrument_app(app, tracer_provider)
    return app
