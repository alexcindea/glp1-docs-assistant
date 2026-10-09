"""Test data: a parsed document without Docling, a fake parser, and database helpers."""

import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.models import (
    BBox,
    Block,
    BlockKind,
    Page,
    ParsedDocument,
    Region,
    Table,
    TableCell,
)
from app.jobs import enqueue
from app.models import Document, DocumentFile, JobKind

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "two-pages.pdf"


def _block(index: int, kind: BlockKind, text: str, page: int, path: list[str]) -> Block:
    return Block(
        index=index,
        kind=kind,
        text=text,
        regions=[
            Region(
                page=page,
                bbox=BBox(x0=0.1, y0=0.1 + index * 0.05, x1=0.9, y1=0.14 + index * 0.05),
                char_start=0,
                char_end=len(text),
            )
        ],
        section_path=path,
        table=Table(
            n_rows=1,
            n_cols=1,
            cells=[
                TableCell(
                    row=0, col=0, row_span=1, col_span=1, text="5 mg", is_header=False, bbox=None
                )
            ],
        )
        if kind == BlockKind.TABLE
        else None,
    )


def fake_parsed() -> ParsedDocument:
    """Two pages, the second scanned: a heading, two paragraphs, a caption and a table."""
    section = ["2 DOSAGE AND ADMINISTRATION"]
    blocks = [
        _block(0, BlockKind.HEADING, "2 DOSAGE AND ADMINISTRATION", 1, section),
        _block(1, BlockKind.PARAGRAPH, "Start at 2.5 mg once weekly.", 1, section),
        _block(2, BlockKind.CAPTION, "Table 1: Strengths", 1, section),
        _block(3, BlockKind.TABLE, "| 5 mg |\n|---|", 1, section),
        _block(4, BlockKind.PARAGRAPH, "Forty-four percent developed antibodies.", 2, section),
    ]
    pages = [
        Page(number=1, width_pt=612, height_pt=792, has_text_layer=True),
        Page(number=2, width_pt=612, height_pt=792, has_text_layer=False),
    ]
    return ParsedDocument(pages=pages, blocks=blocks, parser="fake parser")


class FakeParser:
    """Stands in for Docling: reports progress like it, then returns `fake_parsed()`."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls = 0
        self.fail_with = fail_with

    def parse(
        self,
        path: Path,
        *,
        page_range: tuple[int, int] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ParsedDocument:
        self.calls += 1
        assert path.read_bytes().startswith(b"%PDF")
        if self.fail_with:
            raise self.fail_with
        if on_progress:
            on_progress(1, 2)
            on_progress(2, 2)
        return fake_parsed()


async def insert_document(
    sessions: async_sessionmaker[AsyncSession],
    *,
    content: bytes | None = None,
    kind: JobKind = JobKind.INGEST,
    max_attempts: int = 3,
) -> uuid.UUID:
    """A stored PDF with a queued job, as an upload would leave it."""
    data = content if content is not None else FIXTURE_PDF.read_bytes()
    async with sessions.begin() as session:
        document = Document(
            sha256=hashlib.sha256(data + uuid.uuid4().bytes).hexdigest(),
            filename="fixture.pdf",
            size_bytes=len(data),
            page_count=2,
            file=DocumentFile(content=data),
        )
        session.add(document)
        await session.flush()
        enqueue(session, document.id, kind, max_attempts=max_attempts, pages=2)
        return document.id
