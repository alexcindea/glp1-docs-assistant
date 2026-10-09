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

from app.errors import ApiError, error_responses
from app.models import Document
from app.passages import SearchHit, load_hits
from app.retrieval import retrieve
from app.state import AppStateDep, SessionDep

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    # Only these documents; null or omitted searches all of them.
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


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
    return SearchResponse(query=request.query, hits=await load_hits(session, fused))


async def _check_documents(session: SessionDep, ids: list[uuid.UUID]) -> None:
    found = set(await session.scalars(select(Document.id).where(Document.id.in_(ids))))
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "unknown_documents",
            f"No document with id {', '.join(missing)}. List documents with GET /documents.",
        )
