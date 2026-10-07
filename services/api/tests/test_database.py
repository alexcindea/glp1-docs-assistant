"""The database image and migrations give us working BM25 and vector search.

These exercise the extensions with real queries, not just CREATE EXTENSION:
pg_textsearch only fails at query time if it was not preloaded.
"""

import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import REQUIRED_EXTENSIONS
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
