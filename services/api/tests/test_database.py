"""The database image and migrations give us working BM25 and vector search.

These exercise the extensions with real queries, not just CREATE EXTENSION:
pg_textsearch only fails at query time if it was not preloaded.
"""

import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.db import REQUIRED_EXTENSIONS, Base
from tests.conftest import alembic_config


@pytest.mark.anyio
async def test_bm25_and_vector_search_return_the_expected_rows(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE passages ("
                    " id int PRIMARY KEY, body text NOT NULL, embedding vector(3) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO passages VALUES"
                    " (1, 'The committee approved the annual budget.', '[1,0,0]'),"
                    " (2, 'Store the tablets below 25 degrees, away from light.', '[0,1,0]'),"
                    " (3, 'The bridge was closed for repairs in spring.', '[0,0,1]')"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX passages_bm25 ON passages"
                    " USING bm25(body) WITH (text_config = 'english')"
                )
            )
            bm25_top = await conn.scalar(
                text(
                    "SELECT id FROM passages"
                    " ORDER BY body <@> to_bm25query(:q, 'passages_bm25') LIMIT 1"
                ),
                {"q": "how to store tablets"},
            )
            vector_top = await conn.scalar(
                text("SELECT id FROM passages ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"),
                {"v": "[0.1, 0, 0.9]"},
            )
    finally:
        await engine.dispose()

    assert bm25_top == 2
    assert vector_top == 3


async def _installed_extensions(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT extname FROM pg_extension"))
            return set(rows.scalars())
    finally:
        await engine.dispose()


def test_migrations_downgrade_to_base_and_upgrade_again(database_url: str) -> None:
    config = alembic_config(database_url)
    required = set(REQUIRED_EXTENSIONS)

    command.upgrade(config, "head")
    assert required <= asyncio.run(_installed_extensions(database_url))

    command.downgrade(config, "base")
    assert not required & asyncio.run(_installed_extensions(database_url))

    command.upgrade(config, "head")
    assert required <= asyncio.run(_installed_extensions(database_url))


async def _schema_drift(url: str) -> list[object]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )
    finally:
        await engine.dispose()


def test_models_match_the_migrations(migrated_database_url: str) -> None:
    """A model changed without a migration (or the reverse) fails here, not in production."""
    assert asyncio.run(_schema_drift(migrated_database_url)) == []


async def _insert_span(url: str, x1: float) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            document_id = await conn.scalar(
                text(
                    "INSERT INTO documents (sha256, filename, size_bytes, page_count)"
                    " VALUES (repeat('a', 64), 'x.pdf', 1, 1) RETURNING id"
                )
            )
            chunk_id = await conn.scalar(
                text(
                    "INSERT INTO chunks (document_id, seq, kind, text, section_path,"
                    " token_count, chunker) VALUES (:d, 0, 'text', 'hi', '{}', 1, 't')"
                    " RETURNING id"
                ),
                {"d": document_id},
            )
            await conn.execute(
                text("INSERT INTO chunk_spans VALUES (:c, 0, 1, 0.1, 0.1, :x1, 0.2, 0, 2)"),
                {"c": chunk_id, "x1": x1},
            )
    finally:
        await engine.dispose()


def test_the_database_refuses_a_box_outside_the_page(migrated_database_url: str) -> None:
    asyncio.run(_insert_span(migrated_database_url, x1=0.9))  # inside: accepted
    with pytest.raises(IntegrityError):
        asyncio.run(_insert_span(migrated_database_url, x1=1.2))
