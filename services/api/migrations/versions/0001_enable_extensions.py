"""Enable pgvector and pg_textsearch.

Revision ID: 0001
Revises:
Create Date: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Needs shared_preload_libraries = 'pg_textsearch'; services/db/Dockerfile sets it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_textsearch")
    op.execute("DROP EXTENSION IF EXISTS vector")
