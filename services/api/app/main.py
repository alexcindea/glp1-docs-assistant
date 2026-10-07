"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import health
from app.config import Settings, get_settings
from app.db import create_engine, migration_head
from app.errors import ERROR_RESPONSES, register_error_handlers
from app.state import AppState


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.resources = AppState(engine=engine, expected_revision=migration_head())
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Docs Assistant API",
        version="0.1.0",
        lifespan=lifespan,
        responses=ERROR_RESPONSES,
    )
    register_error_handlers(app)
    app.include_router(health.router)
    return app


app = create_app()
