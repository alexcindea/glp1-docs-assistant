"""Test data: a parsed document without Docling, a fake parser, database helpers, and a
small searchable corpus with a scripted model that cites it (answer, injection and eval
smoke tests)."""

import hashlib
import re
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.embeddings import FakeEmbedder
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
from app.llm import CompletionRequest
from app.models import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    ChunkSpan,
    Document,
    DocumentFile,
    JobKind,
)
from app.retrieval import search_text

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


EMBEDDER = FakeEmbedder(dimensions=EMBEDDING_DIMENSIONS)
DOSE = "The maximum recommended dosage of axolotlin is 2 mg once weekly."
CORPUS = [  # text, page
    (DOSE, 4),
    ("Store axolotlin pens in a refrigerator between 2 and 8 C.", 9),
    ("Nausea was the most common adverse reaction to axolotlin.", 6),
]
_PASSAGE = re.compile(r'<passage id="(\d+)"[^>]*>\n(.*)\n</passage>')


async def add_corpus(
    sessions: async_sessionmaker[AsyncSession], corpus: Sequence[tuple[str, int]] = CORPUS
) -> uuid.UUID:
    """One document whose chunks are `corpus` (text, page), each with one span."""
    document_id = await insert_document(sessions)
    indexed = [search_text(["2 DOSAGE"], text) for text, _ in corpus]
    vectors = await EMBEDDER.embed_documents(indexed)
    async with sessions.begin() as session:
        for seq, ((text, page), search, vector) in enumerate(
            zip(corpus, indexed, vectors, strict=True)
        ):
            span = ChunkSpan(
                seq=0, page=page, x0=0.1, y0=0.2, x1=0.9, y1=0.25, char_start=0, char_end=len(text)
            )
            session.add(
                Chunk(
                    document_id=document_id,
                    seq=seq,
                    kind="text",
                    text=text,
                    section_path=["2 DOSAGE"],
                    search_text=search,
                    embedding=vector,
                    embedding_model=EMBEDDER.name,
                    token_count=len(text.split()),
                    chunker="test",
                    spans=[span],
                )
            )
    return document_id


def passage_number(request: CompletionRequest, containing: str) -> int:
    """Which number the prompt gave the passage holding `containing`."""
    for number, text in _PASSAGE.findall(request.messages[-1].content):
        if containing in text:
            return int(number)
    raise AssertionError(f"no passage contains {containing!r}")


def cites_the_dose(request: CompletionRequest) -> str:
    return f"The maximum dose is 2 mg once weekly [{passage_number(request, 'maximum')}]."
