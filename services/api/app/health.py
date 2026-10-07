"""Liveness and readiness endpoints.

Liveness answers "is the process up". Readiness answers "can it serve requests":
the database is reachable, the required extensions are installed and the schema
is at the migration revision this code expects. Every problem comes with a
message that says what to do about it.
"""

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import REQUIRED_EXTENSIONS, current_revision
from app.state import AppStateDep

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
    ready = not database.problems
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    models = ModelsStatus(
        llm_provider=state.llm.name,
        embedder=state.embedder.name,
        embedding_dimensions=state.embedder.dimensions,
    )
    return ReadinessReport(
        status="ready" if ready else "not_ready", database=database, models=models
    )


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
    except SQLAlchemyError as exc:
        cause = exc.orig if isinstance(exc, DBAPIError) else exc
        return DatabaseStatus(
            reachable=False,
            expected_revision=expected,
            problems=[
                f"Postgres at {where} returned an error ({type(cause).__name__}). "
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
