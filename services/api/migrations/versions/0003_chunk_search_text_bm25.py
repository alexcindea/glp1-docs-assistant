"""chunks.search_text with a BM25 index

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 16:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("search_text", sa.Text(), nullable=True))
    # Existing chunks, written the way app.retrieval.search_text writes new ones.
    op.execute(
        "UPDATE chunks SET search_text = CASE WHEN cardinality(section_path) = 0 THEN text"
        " ELSE array_to_string(section_path, ' > ') || chr(10) || text END"
    )
    op.alter_column("chunks", "search_text", nullable=False)
    op.create_index(
        "ix_chunks_bm25",
        "chunks",
        ["search_text"],
        postgresql_using="bm25",
        postgresql_with={"text_config": "english"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_bm25", table_name="chunks")
    op.drop_column("chunks", "search_text")
