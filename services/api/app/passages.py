"""Retrieved passages with the provenance to show them: text, page and box.

What retrieval (app.retrieval) ranks is chunk ids; this turns them into the
passages that /search returns, that the model reads, and that answers cite.
"""

import uuid
from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.documents import ChunkOut
from app.models import Chunk, Document
from app.retrieval import Fused


class PathRanks(BaseModel):
    """1-based rank in each search path; null when that path didn't find the chunk."""

    vector: int | None
    bm25: int | None


class SearchHit(BaseModel):
    document_id: uuid.UUID
    filename: str
    score: float  # reciprocal rank fusion: higher is better
    ranks: PathRanks
    chunk: ChunkOut


async def load_hits(session: AsyncSession, fused: Sequence[Fused]) -> list[SearchHit]:
    """The fused results with their text, spans and file name, in rank order."""
    rows = await session.execute(
        select(Chunk, Document.filename)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_([f.chunk_id for f in fused]))
        .options(selectinload(Chunk.spans))
    )
    chunks = {chunk.id: (chunk, filename) for chunk, filename in rows}
    hits = []
    for f in fused:
        if f.chunk_id not in chunks:  # replaced by a re-chunk since it was found
            continue
        chunk, filename = chunks[f.chunk_id]
        hits.append(
            SearchHit(
                document_id=chunk.document_id,
                filename=filename,
                score=f.score,
                ranks=PathRanks(vector=f.ranks.get("vector"), bm25=f.ranks.get("bm25")),
                chunk=ChunkOut.model_validate(chunk, from_attributes=True),
            )
        )
    return hits
