"""Liveness and readiness endpoints.

Liveness answers "is the process up". Readiness answers "can it serve requests":
the database is reachable, the required extensions are installed, the schema is
at the migration revision this code expects, and the embedding model is loaded.
Every problem comes with a message that says what to do about it. Warnings don't
stop requests: chunks embedded by another model, which vector search leaves out.
"""

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import (
    DATABASE_ERRORS,
    REQUIRED_EXTENSIONS,
    current_revision,
    database_error_name,
)
from app.models import Chunk
from app.state import AppState, AppStateDep

router = APIRouter(prefix="/health", tags=["health"])

_MIGRATE_HINT = "Run `uv run alembic upgrade head` in services/api."


class LivenessReport(BaseModel):
    status: Literal["ok"] = "ok"


class ExtensionStatus(BaseModel):
    name: str
    installed_version: str | None


class DatabaseStatus(BaseModel):
    reachable: bool
    server_version: str | None = None
    migration_revision: str | None = None
    expected_revision: str | None
    extensions: list[ExtensionStatus] = []
    problems: list[str] = []


class ModelsStatus(BaseModel):
    llm_provider: str
    embedder: str
    embedding_dimensions: int
    problems: list[str] = []
    warnings: list[str] = []


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseStatus
    models: ModelsStatus


@router.get("")
async def liveness() -> LivenessReport:
    return LivenessReport()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessReport}},
)
async def readiness(state: AppStateDep, response: Response) -> ReadinessReport:
    database = await _check_database(state.engine, state.expected_revision)
    models = ModelsStatus(
        llm_provider=state.llm.name,
        embedder=state.embedder.name,
        embedding_dimensions=state.embedder.dimensions,
        problems=_embedder_problems(state),
        warnings=await _other_model_chunks(state) if not database.problems else [],
    )
    ready = not database.problems and not models.problems
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessReport(
        status="ready" if ready else "not_ready", database=database, models=models
    )


def _embedder_problems(state: AppState) -> list[str]:
    loading = state.embedder_loading
    if loading is None:
        return []
    if not loading.done():
        return [f"The embedding model {state.embedder.name} is still loading (seconds)."]
    if not loading.cancelled() and (error := loading.exception()) is not None:
        return [
            f"The embedding model {state.embedder.name} failed to load "
            f"({type(error).__name__}). Images carry it; running natively, it downloads "
            "on first use, so check the network and the Hugging Face cache."
        ]
    return []


async def _other_model_chunks(state: AppState) -> list[str]:
    """Chunks vector search leaves out: no vector, or one from another model."""
    try:
        async with state.sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.embedding_model.is_distinct_from(state.embedder.name))
            )
    except DATABASE_ERRORS:
        return []  # the database check reports it
    if not count:
        return []
    return [
        f"{count} chunks have no vector from {state.embedder.name}, so only keyword search "
        "finds them. Re-chunk their documents with the worker running this EMBEDDER "
        "(`uv run python scripts/ingest_corpus.py --rechunk --workers 1`)."
    ]


async def _check_database(engine: AsyncEngine, expected: str | None) -> DatabaseStatus:
    # Host, port and database name only: never echo credentials back to the client.
    where = f"{engine.url.host}:{engine.url.port or 5432}/{engine.url.database}"

    try:
        async with engine.connect() as conn:
            server_version = await conn.scalar(text("SHOW server_version"))
            rows = await conn.execute(
                text(
                    "SELECT extname, extversion FROM pg_extension WHERE extname IN :names"
                ).bindparams(bindparam("names", expanding=True)),
                {"names": list(REQUIRED_EXTENSIONS)},
            )
            installed: dict[str, str] = {row.extname: row.extversion for row in rows}
            revision = await current_revision(conn)
    except OSError:
        return DatabaseStatus(
            reachable=False,
            expected_revision=expected,
            problems=[
                f"Cannot reach Postgres at {where}. Start it with `docker compose up -d db`."
            ],
        )
    except DATABASE_ERRORS as exc:
        return DatabaseStatus(
            reachable=False,
            expected_revision=expected,
            problems=[
                f"Postgres at {where} returned an error ({database_error_name(exc)}). "
                "Check DATABASE_URL and the credentials in .env."
            ],
        )

    extensions = [
        ExtensionStatus(name=n, installed_version=installed.get(n)) for n in REQUIRED_EXTENSIONS
    ]
    problems = [
        f"Extension `{ext.name}` is not installed. {_MIGRATE_HINT}"
        for ext in extensions
        if ext.installed_version is None
    ]
    if revision != expected:
        problems.append(
            f"Database schema is at revision {revision or 'none'}, "
            f"the code expects {expected}. {_MIGRATE_HINT}"
        )

    return DatabaseStatus(
        reachable=True,
        server_version=server_version,
        migration_revision=revision,
        expected_revision=expected,
        extensions=extensions,
        problems=problems,
    )
