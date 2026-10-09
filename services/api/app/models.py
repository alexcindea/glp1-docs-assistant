"""Database tables. Everything hangs off `documents` and is deleted with it.

Provenance lives in `chunk_spans`: each row maps a character range of a chunk's
text to a page and a box (0-1, origin top left). The database itself refuses a
box outside the page or a backwards range.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# The vector size of the embedding model (Qwen3-Embedding-0.6B, see
# app.local_embedder). Changing the model to one of another size needs a migration;
# app.providers refuses an embedder that doesn't match.
EMBEDDING_DIMENSIONS = 1024


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobKind(StrEnum):
    INGEST = "ingest"  # parse the PDF, then chunk
    RECHUNK = "rechunk"  # chunk again from the stored parse, no OCR


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum_check(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (CheckConstraint(_enum_check("status", DocumentStatus), name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int] = mapped_column(Integer)
    scanned_pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default=DocumentStatus.QUEUED.value)
    # Shown to users: a stable code and a plain message. Exception details stay in jobs.error.
    error_code: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    file: Mapped["DocumentFile"] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentFile(Base):
    """The original PDF bytes, apart from `documents` so listing documents stays cheap."""

    __tablename__ = "document_files"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary)

    document: Mapped[Document] = relationship(back_populates="file")


class PageRecord(Base):
    __tablename__ = "pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    width_pt: Mapped[float] = mapped_column(Float)
    height_pt: Mapped[float] = mapped_column(Float)
    has_text_layer: Mapped[bool] = mapped_column(Boolean)


class ParsedRecord(Base):
    """The whole ParsedDocument as JSON: chunking can be re-run without parsing again."""

    __tablename__ = "parsed_documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    parsed: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TableRecord(Base):
    """A table's cells with boxes, for display. Its Markdown is searched through its chunk."""

    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("document_id", "block_index"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    block_index: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str] = mapped_column(Text)
    n_rows: Mapped[int] = mapped_column(Integer)
    n_cols: Mapped[int] = mapped_column(Integer)
    cells: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "seq"),
        CheckConstraint("kind IN ('text', 'table')", name="kind"),
        # BM25 (pg_textsearch). Queried as `search_text <@> to_bm25query(q, 'ix_chunks_bm25')`.
        Index(
            "ix_chunks_bm25",
            "search_text",
            postgresql_using="bm25",
            postgresql_with={"text_config": "english"},
        ),
        # Approximate nearest neighbours by cosine distance (`embedding <=> query`).
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # Short integer ids: they are what the model cites in phase 5.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)  # order within the document
    kind: Mapped[str] = mapped_column(String(16))  # "text" or "table"
    text: Mapped[str] = mapped_column(Text)  # the citeable text
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # What retrieval indexes: section path + text (app.retrieval.search_text). Never cited.
    search_text: Mapped[str] = mapped_column(Text)
    # search_text embedded by `embedding_model`. Null only for chunks stored before
    # embeddings existed; the worker always writes chunks with their vectors.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    token_count: Mapped[int] = mapped_column(Integer)
    table_id: Mapped[int | None] = mapped_column(ForeignKey("tables.id", ondelete="SET NULL"))
    chunker: Mapped[str] = mapped_column(String(32))  # version that produced it

    spans: Mapped[list["ChunkSpan"]] = relationship(
        order_by="ChunkSpan.seq", cascade="all, delete-orphan", passive_deletes=True
    )


class ChunkSpan(Base):
    """One piece of a chunk's text, on one page, in one box."""

    __tablename__ = "chunk_spans"
    __table_args__ = (
        CheckConstraint("page >= 1", name="page"),
        CheckConstraint("0 <= x0 AND x0 <= x1 AND x1 <= 1", name="x_inside_page"),
        CheckConstraint("0 <= y0 AND y0 <= y1 AND y1 <= 1", name="y_inside_page"),
        CheckConstraint("0 <= char_start AND char_start <= char_end", name="char_range"),
    )

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    page: Mapped[int] = mapped_column(Integer)
    x0: Mapped[float] = mapped_column(Float)
    y0: Mapped[float] = mapped_column(Float)
    x1: Mapped[float] = mapped_column(Float)
    y1: Mapped[float] = mapped_column(Float)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)


class Job(Base):
    """Ingest queue. Workers claim rows with FOR UPDATE SKIP LOCKED (see app.jobs)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_enum_check("kind", JobKind), name="kind"),
        CheckConstraint(_enum_check("status", JobStatus), name="status"),
        Index("ix_jobs_claimable", "run_after", postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_document_id", "document_id"),
        # At most one queued or running job per document: two at once would race on the
        # replace-all writes (a double-clicked re-chunk, two uploads of a failed file).
        Index(
            "uq_jobs_one_active_per_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="3")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_done: Mapped[int] = mapped_column(Integer, server_default="0")
    progress_total: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
