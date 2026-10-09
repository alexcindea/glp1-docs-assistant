"""documents, chunks with provenance, and the job queue

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 11:53:34.513570
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("scanned_pages", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name=op.f("ck_documents_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("sha256", name=op.f("uq_documents_sha256")),
    )
    op.create_table(
        "document_files",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_files_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_document_files")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_done", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('ingest', 'rechunk')", name=op.f("ck_jobs_kind")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name=op.f("ck_jobs_status")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_jobs_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["run_after"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("ix_jobs_document_id", "jobs", ["document_id"], unique=False)
    op.create_index(
        "uq_jobs_one_active_per_document",
        "jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "pages",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("width_pt", sa.Float(), nullable=False),
        sa.Column("height_pt", sa.Float(), nullable=False),
        sa.Column("has_text_layer", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_pages_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "number", name=op.f("pk_pages")),
    )
    op.create_table(
        "parsed_documents",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_parsed_documents_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", name=op.f("pk_parsed_documents")),
    )
    op.create_table(
        "tables",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("n_rows", sa.Integer(), nullable=False),
        sa.Column("n_cols", sa.Integer(), nullable=False),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_tables_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tables")),
        sa.UniqueConstraint("document_id", "block_index", name=op.f("uq_tables_document_id")),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=True),
        sa.Column("chunker", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["tables.id"], name=op.f("fk_chunks_table_id_tables"), ondelete="SET NULL"
        ),
        sa.CheckConstraint("kind IN ('text', 'table')", name=op.f("ck_chunks_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("document_id", "seq", name=op.f("uq_chunks_document_id")),
    )
    op.create_table(
        "chunk_spans",
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Float(), nullable=False),
        sa.Column("y0", sa.Float(), nullable=False),
        sa.Column("x1", sa.Float(), nullable=False),
        sa.Column("y1", sa.Float(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "0 <= char_start AND char_start <= char_end", name=op.f("ck_chunk_spans_char_range")
        ),
        sa.CheckConstraint(
            "0 <= x0 AND x0 <= x1 AND x1 <= 1", name=op.f("ck_chunk_spans_x_inside_page")
        ),
        sa.CheckConstraint(
            "0 <= y0 AND y0 <= y1 AND y1 <= 1", name=op.f("ck_chunk_spans_y_inside_page")
        ),
        sa.CheckConstraint("page >= 1", name=op.f("ck_chunk_spans_page")),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_chunk_spans_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "seq", name=op.f("pk_chunk_spans")),
    )


def downgrade() -> None:
    op.drop_table("chunk_spans")
    op.drop_table("chunks")
    op.drop_table("tables")
    op.drop_table("parsed_documents")
    op.drop_table("pages")
    op.drop_index("uq_jobs_one_active_per_document", table_name="jobs")
    op.drop_index("ix_jobs_document_id", table_name="jobs")
    op.drop_index(
        "ix_jobs_claimable", table_name="jobs", postgresql_where=sa.text("status = 'queued'")
    )
    op.drop_table("jobs")
    op.drop_table("document_files")
    op.drop_table("documents")
