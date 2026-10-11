"""Retrieval: BM25 against a real Postgres, and reciprocal rank fusion."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.embeddings import FakeEmbedder
from app.models import EMBEDDING_DIMENSIONS, Chunk
from app.passages import load_hits
from app.retrieval import (
    Fused,
    bm25_search,
    fuse,
    retrieve,
    rrf_fuse,
    search_text,
    vector_search,
)
from tests.factories import insert_document

EMBEDDER = FakeEmbedder(dimensions=EMBEDDING_DIMENSIONS)


async def add_chunks(
    sessions: async_sessionmaker[AsyncSession], document_id: uuid.UUID, texts: list[str]
) -> list[int]:
    indexed = [search_text(["1 Section"], text) for text in texts]
    vectors = await EMBEDDER.embed_documents(indexed)
    async with sessions.begin() as session:
        chunks = [
            Chunk(
                document_id=document_id,
                seq=seq,
                kind="text",
                text=text,
                section_path=["1 Section"],
                search_text=search,
                embedding=vector,
                embedding_model=EMBEDDER.name,
                token_count=len(text.split()),
                chunker="test",
            )
            for seq, (text, search, vector) in enumerate(zip(texts, indexed, vectors, strict=True))
        ]
        session.add_all(chunks)
        await session.flush()
        return [chunk.id for chunk in chunks]


async def use_the_vector_index(session: AsyncSession) -> None:
    """A small test table gets an exact scan; a real corpus gets the HNSW index, whose
    limits are what these tests are about. Make the planner choose it here too."""
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_sort = off"))
    plan = await session.scalars(
        text("EXPLAIN SELECT id FROM chunks ORDER BY embedding <=> CAST(:v AS vector) LIMIT 5"),
        {"v": str([1.0] * EMBEDDING_DIMENSIONS)},
    )
    assert any("ix_chunks_embedding" in line for line in plan)


async def use_the_bm25_index(session: AsyncSession) -> None:
    """As for vectors: the real corpus is searched through the BM25 index, so test that."""
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = await session.scalars(
        text(
            "EXPLAIN SELECT id FROM chunks"
            " ORDER BY search_text <@> to_bm25query('x', 'ix_chunks_bm25') LIMIT 5"
        )
    )
    assert any("ix_chunks_bm25" in line for line in plan)


def test_search_text_puts_the_section_path_first() -> None:
    assert search_text(["2 DOSAGE", "2.1 Starting dose"], "Start at 2.5 mg.") == (
        "2 DOSAGE > 2.1 Starting dose\nStart at 2.5 mg."
    )
    assert search_text([], "Untitled text.") == "Untitled text."


@pytest.mark.db
@pytest.mark.anyio
async def test_bm25_ranks_rare_query_terms_first_and_skips_non_matches(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document = await insert_document(sessions)
    pen, generic, unrelated = await add_chunks(
        sessions,
        document,
        [
            "Discard the quokkapen 56 days after first use.",
            "Patients should discard needles after each use.",
            "The committee met on Tuesday.",
        ],
    )

    async with sessions() as session:
        found = await bm25_search(session, "when to discard the quokkapen", 10, [document])

    assert found == [pen, generic]
    assert unrelated not in found


@pytest.mark.db
@pytest.mark.anyio
async def test_bm25_filters_to_the_chosen_documents_even_when_they_rank_low(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    strong, weak = await insert_document(sessions), await insert_document(sessions)
    await add_chunks(sessions, strong, ["wombatine wombatine wombatine dosing"] * 30)
    (weak_chunk,) = await add_chunks(sessions, weak, ["A long passage " * 40 + "wombatine."])

    async with sessions() as session:
        await use_the_bm25_index(session)
        everywhere = await bm25_search(session, "wombatine", 10)
        only_weak = await bm25_search(session, "wombatine", 10, [weak])

    assert weak_chunk not in everywhere
    assert only_weak == [weak_chunk]


@pytest.mark.db
@pytest.mark.anyio
async def test_bm25_returns_nothing_for_words_no_chunk_contains(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document = await insert_document(sessions)
    await add_chunks(sessions, document, ["Store below 25 C."])

    async with sessions() as session:
        assert await bm25_search(session, "xylophonics zeppelinry", 1000) == []


@pytest.mark.db
@pytest.mark.anyio
async def test_vector_search_returns_more_rows_than_the_index_default(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document = await insert_document(sessions)
    await add_chunks(sessions, document, [f"gecko sample number {i}" for i in range(70)])
    query = await EMBEDDER.embed_query("gecko sample")

    async with sessions() as session:
        await use_the_vector_index(session)
        found = await vector_search(session, query, EMBEDDER.name, 60)  # the whole corpus
        settings = [
            await session.scalar(text(f"SHOW {name}"))
            for name in ("hnsw.ef_search", "hnsw.iterative_scan")
        ]

    assert settings == ["60", "strict_order"]  # applied for this transaction
    # HNSW alone stops at ef_search = 40 (41 rows without the settings). The index is
    # approximate, so a crowded test database can cost a row or two of the 60.
    assert len(found) > 50


@pytest.mark.db
@pytest.mark.anyio
async def test_vector_search_filters_to_the_chosen_documents_even_when_they_rank_low(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # A filtered search is exact. Through the index it once returned nothing in CI:
    # HNSW can leave a vector unreachable, and a filtered scan never finds it.
    strong, weak = await insert_document(sessions), await insert_document(sessions)
    await add_chunks(sessions, strong, [f"narwhal tusk length {i}" for i in range(200)])
    (weak_chunk,) = await add_chunks(sessions, weak, ["Unrelated words entirely, narwhal once."])
    query = await EMBEDDER.embed_query("narwhal tusk length")

    async with sessions() as session:
        await use_the_vector_index(session)
        everywhere = await vector_search(session, query, EMBEDDER.name, 5)
        only_weak = await vector_search(session, query, EMBEDDER.name, 5, [weak])
        both = await vector_search(session, query, EMBEDDER.name, 300, [strong, weak])

    assert weak_chunk not in everywhere
    assert only_weak == [weak_chunk]
    assert len(both) == 201  # every chunk of the chosen documents, none missed
    assert both[-1] == weak_chunk


@pytest.mark.db
@pytest.mark.anyio
async def test_vector_search_ignores_vectors_from_another_model(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document = await insert_document(sessions)
    ours, theirs = await add_chunks(sessions, document, ["okapi stripes", "okapi stripes"])
    async with sessions.begin() as session:
        other = await session.get(Chunk, theirs)
        assert other is not None
        other.embedding_model = "another/model@abc"
    query = await EMBEDDER.embed_query("okapi stripes")

    async with sessions() as session:
        found = await vector_search(session, query, EMBEDDER.name, 10, [document])

    assert found == [ours]


@pytest.mark.db
@pytest.mark.anyio
async def test_retrieve_fuses_both_paths_and_reports_each_rank(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document = await insert_document(sessions)
    ids = await add_chunks(
        sessions,
        document,
        [
            "Store the axolotlpen in a refrigerator.",
            "The axolotlpen may be kept at room temperature for 21 days.",
            "Nausea was common.",
        ],
    )

    async with sessions() as session:
        fused = await retrieve(
            session, EMBEDDER, "axolotlpen room temperature", limit=2, document_ids=[document]
        )

    assert [f.chunk_id for f in fused] == [ids[1], ids[0]]
    assert fused[0].ranks == {"vector": 1, "bm25": 1}


@pytest.mark.db
@pytest.mark.anyio
async def test_fuse_keeps_one_copy_of_a_passage_a_document_repeats(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    label, other_label = await insert_document(sessions), await insert_document(sessions)
    first, repeat, different = await add_chunks(
        sessions, label, ["Store below 30 C.", "Store below 30 C.", "Discard after 56 days."]
    )
    (same_text_elsewhere,) = await add_chunks(sessions, other_label, ["Store below 30 C."])
    rankings = {"bm25": [first, repeat, same_text_elsewhere, different]}

    async with sessions() as session:
        fused = await fuse(session, rankings, limit=10)
        top_two = await fuse(session, rankings, limit=2)

    assert [f.chunk_id for f in fused] == [first, same_text_elsewhere, different]
    assert [f.chunk_id for f in top_two] == [first, same_text_elsewhere]


def test_rrf_prefers_a_chunk_both_paths_found_over_one_path_first() -> None:
    fused = rrf_fuse({"vector": [1, 2, 3], "bm25": [4, 2, 5]})

    assert [f.chunk_id for f in fused][:3] == [2, 1, 4]
    assert fused[0].ranks == {"vector": 2, "bm25": 2}
    assert fused[0].score == pytest.approx(2 / 62)


def test_rrf_with_one_path_keeps_its_order() -> None:
    assert [f.chunk_id for f in rrf_fuse({"bm25": [7, 3, 9], "vector": []})] == [7, 3, 9]


def test_rrf_counts_a_repeated_chunk_once_at_its_best_rank() -> None:
    (fused,) = rrf_fuse({"bm25": [5, 5, 5]})
    assert fused.ranks == {"bm25": 1}
    assert fused.score == pytest.approx(1 / 61)


def test_rrf_breaks_ties_deterministically() -> None:
    # 8 and 9 both score 1/61 + 1/63: the lower id comes first, in either input order.
    for rankings in (
        {"vector": [9, 1, 8], "bm25": [8, 2, 9]},
        {"bm25": [9, 2, 8], "vector": [8, 1, 9]},
    ):
        fused = rrf_fuse(rankings)
        assert fused[0].score == fused[1].score
        assert [f.chunk_id for f in fused[:2]] == [8, 9]


def test_rrf_of_nothing_is_empty() -> None:
    assert rrf_fuse({"vector": [], "bm25": []}) == []


@pytest.mark.db
@pytest.mark.anyio
async def test_a_chunk_replaced_after_it_was_found_is_skipped_not_an_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # A re-chunk can replace a document's chunks between searching and loading them.
    document = await insert_document(sessions)
    (kept,) = await add_chunks(sessions, document, ["Store the axolotlpen cold."])
    replaced = kept + 1000

    async with sessions() as session:
        hits = await load_hits(session, [Fused(replaced, 0.03), Fused(kept, 0.02)])

    assert [hit.chunk.id for hit in hits] == [kept]
