"""Writes ingest results. Everything for a document is replaced as a whole, inside the
caller's transaction, so a retried or re-chunked job never leaves old and new rows mixed.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Vector
from app.ingest.chunking import CHUNKER_VERSION, ChunkDraft
from app.ingest.models import BlockKind, ParsedDocument
from app.models import Chunk, ChunkSpan, PageRecord, ParsedRecord, TableRecord
from app.retrieval import search_text


async def save_parse(session: AsyncSession, document_id: uuid.UUID, parsed: ParsedDocument) -> None:
    """Pages, the parsed document (for re-chunking) and tables (for display)."""
    for model in (Chunk, TableRecord, PageRecord, ParsedRecord):
        await session.execute(delete(model).where(model.document_id == document_id))
    session.add_all(
        PageRecord(
            document_id=document_id,
            number=page.number,
            width_pt=page.width_pt,
            height_pt=page.height_pt,
            has_text_layer=page.has_text_layer,
        )
        for page in parsed.pages
    )
    session.add(ParsedRecord(document_id=document_id, parsed=parsed.model_dump(mode="json")))
    for position, block in enumerate(parsed.blocks):
        if block.kind != BlockKind.TABLE or block.table is None:
            continue
        session.add(
            TableRecord(
                document_id=document_id,
                block_index=block.index,
                page=block.regions[0].page,
                caption=_caption(parsed, position),
                markdown=block.text,
                n_rows=block.table.n_rows,
                n_cols=block.table.n_cols,
                cells=[cell.model_dump(mode="json") for cell in block.table.cells],
            )
        )
    await session.flush()


def chunk_search_texts(chunks: Sequence[ChunkDraft]) -> list[str]:
    """What gets indexed (BM25) and embedded for each chunk."""
    return [search_text(draft.section_path, draft.text) for draft in chunks]


async def stored_embeddings(
    session: AsyncSession, document_id: uuid.UUID, model: str
) -> dict[str, Vector]:
    """The document's current vectors by search text, for re-chunking to reuse: a chunk
    whose indexed text didn't change keeps its vector instead of being embedded again."""
    rows = await session.execute(
        select(Chunk.search_text, Chunk.embedding).where(
            Chunk.document_id == document_id,
            Chunk.embedding_model == model,
            Chunk.embedding.is_not(None),
        )
    )
    return {text: embedding for text, embedding in rows if embedding is not None}


async def save_chunks(
    session: AsyncSession,
    document_id: uuid.UUID,
    chunks: list[ChunkDraft],
    embeddings: Sequence[Vector],
    embedding_model: str,
) -> None:
    """Replaces the document's chunks, each with its vector (`embeddings` in the same
    order). Tables must already be saved (chunks reference them)."""
    if len(embeddings) != len(chunks):
        raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    rows = await session.execute(
        select(TableRecord.block_index, TableRecord.id).where(
            TableRecord.document_id == document_id
        )
    )
    # Not dict(rows): a Result has .keys(), so dict() treats it as a mapping and fails.
    tables = {block_index: table_id for block_index, table_id in rows}  # noqa: C416
    texts = chunk_search_texts(chunks)
    for seq, (draft, text, embedding) in enumerate(zip(chunks, texts, embeddings, strict=True)):
        session.add(
            Chunk(
                document_id=document_id,
                seq=seq,
                kind=draft.kind,
                text=draft.text,
                section_path=draft.section_path,
                search_text=text,
                embedding=embedding,
                embedding_model=embedding_model,
                token_count=draft.token_count,
                table_id=tables.get(draft.table_block) if draft.table_block is not None else None,
                chunker=CHUNKER_VERSION,
                spans=[
                    ChunkSpan(
                        seq=i,
                        page=span.page,
                        x0=span.bbox.x0,
                        y0=span.bbox.y0,
                        x1=span.bbox.x1,
                        y1=span.bbox.y1,
                        char_start=span.char_start,
                        char_end=span.char_end,
                    )
                    for i, span in enumerate(draft.spans)
                ],
            )
        )
    await session.flush()


def _caption(parsed: ParsedDocument, position: int) -> str | None:
    """The caption right before or after a table, if there is one."""
    for neighbour in (position - 1, position + 1):
        if 0 <= neighbour < len(parsed.blocks):
            block = parsed.blocks[neighbour]
            if block.kind == BlockKind.CAPTION:
                return block.text
    return None
