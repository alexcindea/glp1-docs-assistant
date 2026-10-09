import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.db import create_engine, migration_head
from app.embeddings import FakeEmbedder
from app.factory import create_app
from app.health import _embedder_problems
from app.models import EMBEDDING_DIMENSIONS
from app.state import AppState


def _sql(database_url: str, statement: str) -> None:
    async def run() -> None:
        engine = create_engine(Settings(database_url=database_url))
        async with engine.begin() as conn:
            await conn.execute(text(statement))
        await engine.dispose()

    asyncio.run(run())


# Nothing listens on port 1, so connecting fails immediately.
UNREACHABLE_URL = "postgresql+asyncpg://app:s3cret-password@127.0.0.1:1/app"


def make_client(database_url: str) -> TestClient:
    settings = Settings(database_url=database_url, db_connect_timeout_seconds=2)
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_liveness_does_not_need_the_database() -> None:
    with make_client(UNREACHABLE_URL) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_is_migrated(migrated_database_url: str) -> None:
    with make_client(migrated_database_url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    database = body["database"]
    assert database["problems"] == []
    assert database["migration_revision"] == migration_head()
    assert {e["name"]: bool(e["installed_version"]) for e in database["extensions"]} == {
        "vector": True,
        "pg_textsearch": True,
    }
    models = body["models"]
    assert (models["llm_provider"], models["embedder"]) == ("fake", "fake")
    assert models["embedding_dimensions"] == EMBEDDING_DIMENSIONS
    assert models["problems"] == []


def test_chunks_without_a_vector_from_this_model_warn_but_do_not_block(
    migrated_database_url: str,
) -> None:
    _sql(
        migrated_database_url,
        "WITH d AS (INSERT INTO documents (sha256, filename, size_bytes, page_count)"
        " VALUES (md5(random()::text) || md5(random()::text), 'old.pdf', 1, 1) RETURNING id)"
        " INSERT INTO chunks (document_id, seq, kind, text, section_path, search_text,"
        " token_count, chunker) SELECT id, 0, 'text', 'hi', '{}', 'hi', 1, 't' FROM d",
    )
    with make_client(migrated_database_url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    (warning,) = response.json()["models"]["warnings"]
    assert "only keyword search finds them" in warning


@pytest.mark.anyio
async def test_the_embedding_model_must_be_loaded_to_be_ready() -> None:
    async def fail() -> None:
        raise OSError("weights missing")

    loading = asyncio.create_task(asyncio.sleep(10))
    failed = asyncio.create_task(fail())
    await asyncio.gather(failed, return_exceptions=True)
    done = asyncio.create_task(asyncio.sleep(0))
    await done

    def state(task: "asyncio.Task[None] | None") -> AppState:
        return cast(AppState, SimpleNamespace(embedder=FakeEmbedder(), embedder_loading=task))

    try:
        assert "still loading" in _embedder_problems(state(loading))[0]
        assert "failed to load (OSError)" in _embedder_problems(state(failed))[0]
        assert _embedder_problems(state(done)) == []
        assert _embedder_problems(state(None)) == []
    finally:
        loading.cancel()


def test_not_ready_before_migrations(database_url: str) -> None:
    with make_client(database_url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    database = response.json()["database"]
    assert database["reachable"] is True
    assert database["migration_revision"] is None
    assert all(e["installed_version"] is None for e in database["extensions"])
    assert database["problems"]
    assert all("alembic upgrade head" in p for p in database["problems"])


def test_not_ready_when_database_is_unreachable() -> None:
    with make_client(UNREACHABLE_URL) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    database = response.json()["database"]
    assert database["reachable"] is False
    assert "docker compose up -d db" in database["problems"][0]
    assert "s3cret-password" not in response.text


def test_not_ready_when_postgres_rejects_the_connection(
    rejected_database_url: tuple[str, str],
) -> None:
    url, error = rejected_database_url

    with make_client(url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    (problem,) = response.json()["database"]["problems"]
    assert error in problem
    assert "credentials" in problem
    assert "wrong-password" not in response.text
