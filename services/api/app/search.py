"""Search: the chunks that best match a query, with the provenance to show them.

Hybrid retrieval (app.retrieval) over every ready document, or only the ones the
caller names. Each hit carries the chunk's spans (page and box), so a client can
highlight it, and its rank in each search path, so it can show why it was found.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.documents import ChunkOut
from app.errors import ApiError, error_responses
from app.models import Chunk, Document
from app.retrieval import retrieve
from app.state import AppStateDep, SessionDep

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    # Only these documents; null or omitted searches all of them.
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


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


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


@router.post("", responses=error_responses(404))
async def search(request: SearchRequest, state: AppStateDep, session: SessionDep) -> SearchResponse:
    if request.document_ids is not None:
        await _check_documents(session, request.document_ids)
    fused = await retrieve(
        session,
        state.embedder,
        request.query,
        limit=request.limit,
        document_ids=request.document_ids,
    )
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
    return SearchResponse(query=request.query, hits=hits)


async def _check_documents(session: SessionDep, ids: list[uuid.UUID]) -> None:
    found = set(await session.scalars(select(Document.id).where(Document.id.in_(ids))))
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_documents",
            f"No document with id {', '.join(missing)}. List documents with GET /documents.",
        )
