"""Database engine, ORM base and migration metadata."""

from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, MetaData
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Extensions the app cannot run without; the first migration creates them.
REQUIRED_EXTENSIONS = ("vector", "pg_textsearch")

# Stable constraint names so Alembic autogenerate produces predictable migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": settings.db_connect_timeout_seconds},
    )


def migration_head() -> str | None:
    """The newest migration revision in the code."""
    return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()


async def current_revision(conn: AsyncConnection) -> str | None:
    """The migration revision the database is at, or None if never migrated."""

    def _read(sync_conn: Connection) -> str | None:
        return MigrationContext.configure(sync_conn).get_current_revision()

    return await conn.run_sync(_read)
