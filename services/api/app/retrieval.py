"""Finding the chunks that answer a question.

Two independent searches rank chunks, and reciprocal rank fusion (RRF) merges
the rankings:

- BM25 (pg_textsearch) rewards the query's exact words, weighted by how rare
  they are: drug names, doses, "C-cell". Strong where documents differ by one
  name and are otherwise near-identical (one label per GLP-1 drug).
- Vector search (pgvector) matches meaning: "how long can the pen stay out of
  the fridge" finds "may be stored unrefrigerated for up to 21 days".

RRF uses ranks only: a chunk scores sum(1 / (k + rank)) over the paths that
found it. BM25 scores and cosine similarities live on unrelated scales, and
ranks sidestep normalising them. k = 60 (from the original paper) flattens the
curve, so being found by both paths beats being first in one. Word-for-word
repeats of a passage within a document are then dropped (see `fuse`).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from opentelemetry import trace
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Embedder, Vector
from app.models import Chunk

tracer = trace.get_tracer(__name__)

BM25_INDEX = "ix_chunks_bm25"
RRF_K = 60
CANDIDATES = 50  # how deep each path searches before fusion


def search_text(section_path: Sequence[str], text: str) -> str:
    """What retrieval indexes for a chunk: where it sits, then what it says.

    A label's "Nausea | 4 | 18" row or a short "Discard the pen after 30 days"
    means little without "6.1 Clinical Trials Experience" or "16 HOW SUPPLIED"
    above it. Only the search sees this; the citeable text is stored unchanged.
    """
    if not section_path:
        return text
    return f"{' > '.join(section_path)}\n{text}"


async def bm25_search(
    session: AsyncSession,
    query: str,
    limit: int,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list[int]:
    """Chunk ids, best first. Only chunks containing at least one query term."""
    # pg_textsearch scores are negative; lower is better, 0 means no term matched.
    score = Chunk.search_text.op("<@>")(func.to_bm25query(query, BM25_INDEX))
    statement = select(Chunk.id, score).order_by(score).limit(limit)
    if document_ids is not None:
        statement = statement.where(Chunk.document_id.in_(document_ids))
    rows = await session.execute(statement)
    # With a large LIMIT the index also returns non-matching rows (score 0).
    return [chunk_id for chunk_id, value in rows if value < 0]


async def vector_search(
    session: AsyncSession,
    query: Vector,
    model: str,
    limit: int,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list[int]:
    """Chunk ids by cosine similarity to the query vector, best first.

    Only chunks embedded by `model` (the one that embedded the query) take part:
    vectors from two models live in unrelated spaces, so comparing them would
    rank by noise. Readiness warns about chunks left out this way.

    Searching chosen documents is exact: their chunks are read first and every
    distance computed (a few ms for a 1,000-chunk document). The HNSW index is
    approximate, and a chunk it can't reach from its entry point is never returned,
    however far a filtered scan goes; seen in CI with near-identical test vectors.

    Searching everything uses the HNSW index, which returns at most
    `hnsw.ef_search` rows (default 40), so that is raised to the limit for this
    transaction, with iterative scanning (pgvector 0.8) so that rows from another
    model, which the index still holds, don't leave the result short.
    """
    current = Chunk.embedding.is_not(None), Chunk.embedding_model == model
    if document_ids is not None:
        chosen = (
            select(Chunk.id, Chunk.embedding)
            .where(*current, Chunk.document_id.in_(document_ids))
            .cte("chosen")
            .prefix_with("MATERIALIZED")  # read them, rather than walk the index
        )
        exact = select(chosen.c.id).order_by(chosen.c.embedding.cosine_distance(query)).limit(limit)
        return list(await session.scalars(exact))
    await session.execute(
        select(
            func.set_config("hnsw.ef_search", str(max(limit, 40)), True),
            func.set_config("hnsw.iterative_scan", "strict_order", True),
        )
    )
    statement = (
        select(Chunk.id)
        .where(*current)
        .order_by(Chunk.embedding.cosine_distance(query))
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def retrieve(
    session: AsyncSession,
    embedder: Embedder,
    query: str,
    *,
    limit: int,
    candidates: int = CANDIDATES,
    document_ids: Sequence[uuid.UUID] | None = None,
) -> list["Fused"]:
    """Both searches, fused: the `limit` best chunks for the query.

    Traced as one span per step, so a slow question shows which step was slow.
    """
    with tracer.start_as_current_span("retrieve") as span:
        span.set_attributes(
            {
                "retrieval.query_chars": len(query),
                "retrieval.limit": limit,
                "retrieval.candidates": candidates,
                "retrieval.document_filter": len(document_ids) if document_ids else 0,
            }
        )
        with tracer.start_as_current_span("embed_query") as step:
            step.set_attribute("embedding.model", embedder.name)
            query_vector = await embedder.embed_query(query)
        with tracer.start_as_current_span("vector_search") as step:
            vector = await vector_search(
                session, query_vector, embedder.name, candidates, document_ids
            )
            step.set_attribute("retrieval.hits", len(vector))
        with tracer.start_as_current_span("bm25_search") as step:
            bm25 = await bm25_search(session, query, candidates, document_ids)
            step.set_attribute("retrieval.hits", len(bm25))
        fused = await fuse(session, {"vector": vector, "bm25": bm25}, limit)
        span.set_attribute("retrieval.results", len(fused))
        return fused


async def fuse(
    session: AsyncSession, rankings: Mapping[str, Sequence[int]], limit: int
) -> list["Fused"]:
    """RRF, then repeats dropped: the `limit` best distinct chunks.

    Documents can repeat a passage word for word (product information repeats
    its storage section for every pen and vial). Identical chunks get identical
    scores and would fill several of the top places with one passage. Within a
    document, only the best-ranked copy is kept; the same text in two documents is
    two sources, so both stay.
    """
    fused = rrf_fuse(rankings)
    rows = await session.execute(
        select(Chunk.id, Chunk.document_id, func.md5(Chunk.search_text)).where(
            Chunk.id.in_([f.chunk_id for f in fused])
        )
    )
    passage = {chunk_id: (document_id, digest) for chunk_id, document_id, digest in rows}
    seen: set[tuple[uuid.UUID, str]] = set()
    distinct = []
    for f in fused:
        key = passage.get(f.chunk_id)
        if key is None or key in seen:  # gone (re-chunked meanwhile) or a repeat
            continue
        seen.add(key)
        distinct.append(f)
        if len(distinct) == limit:
            break
    return distinct


@dataclass(frozen=True)
class Fused:
    chunk_id: int
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # path -> 1-based rank in it


def rrf_fuse(rankings: Mapping[str, Sequence[int]], k: int = RRF_K) -> list[Fused]:
    """Merge rankings (path name -> chunk ids, best first) into one, best first.

    A chunk listed twice in one ranking counts once, at its better rank. Ties
    (same score) go to the lower chunk id, so the order is deterministic.
    """
    ranks: dict[int, dict[str, int]] = {}
    for path, ranking in rankings.items():
        for rank, chunk_id in enumerate(ranking, 1):
            ranks.setdefault(chunk_id, {}).setdefault(path, rank)
    fused = [
        Fused(chunk_id, sum(1 / (k + rank) for rank in by_path.values()), by_path)
        for chunk_id, by_path in ranks.items()
    ]
    fused.sort(key=lambda f: (-f.score, f.chunk_id))
    return fused
