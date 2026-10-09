"""chunks.embedding (Qwen3-Embedding-0.6B, 1024 dimensions) with an HNSW index

Existing chunks get no vector here: re-chunking them (POST /documents/{id}/rechunk,
or scripts/ingest_corpus.py --rechunk) embeds them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 16:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("embedding", Vector(1024), nullable=True))
    op.add_column("chunks", sa.Column("embedding_model", sa.String(length=128), nullable=True))
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding", table_name="chunks")
    op.drop_column("chunks", "embedding_model")
    op.drop_column("chunks", "embedding")
