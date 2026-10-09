"""FastAPI application factory. Importing this module has no side effects."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from app import answers, documents, health, search
from app.config import Settings, get_settings
from app.db import create_engine, create_sessionmaker, migration_head
from app.errors import ERROR_RESPONSES, register_error_handlers
from app.local_embedder import TransformersEmbedder
from app.observability import instrument_app, instrument_engine, uninstrument_engines
from app.providers import build_embedder, build_llm_provider
from app.state import AppState

logger = structlog.get_logger("app.factory")


def _log_load_failure(task: "asyncio.Task[None]") -> None:
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("embedder.load_failed", error=f"{type(error).__name__}: {error}")


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
        embedder = build_embedder(settings)
        # Load the local embedding model in the background (seconds): the API reports
        # not ready until it is loaded, and a search that comes first waits for it.
        loading = (
            asyncio.create_task(asyncio.to_thread(embedder.load))
            if isinstance(embedder, TransformersEmbedder)
            else None
        )
        if loading:
            loading.add_done_callback(_log_load_failure)
        app.state.resources = AppState(
            settings=settings,
            engine=engine,
            sessions=create_sessionmaker(engine),
            expected_revision=migration_head(),
            llm=build_llm_provider(settings),
            embedder=embedder,
            embedder_loading=loading,
        )
        try:
            yield
        finally:
            if loading:
                await asyncio.gather(loading, return_exceptions=True)
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
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(answers.router)
    app.add_middleware(
        documents.UploadSizeLimit,
        max_bytes=documents.upload_limit_bytes(settings.max_upload_mb),
    )
    if tracer_provider:
        instrument_app(app, tracer_provider)
    return app
