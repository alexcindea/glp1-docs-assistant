"""Pick the embedding model by recall on the gold set.

Usage (from the repo root, with the dev database holding the ingested corpus):
    uv run python evals/embedder_bakeoff.py

Embeds every stored chunk with each shortlisted model, twice: the chunk text
alone, and with its section path in front (app.retrieval.search_text). Each
answerable gold question is embedded as a query and scored with
evals/metrics.py two ways:

- vector: the top 10 chunks by cosine similarity (exact, in memory, no index),
  so the comparison is about the models, not the database;
- fused: those vector results merged with the database's BM25 results by
  reciprocal rank fusion, as the app will search. This is the number that
  decides: a model that only finds what BM25 already finds adds little.

Chunk vectors are cached in evals/.cache/ (ignored by git), keyed by model and
the exact texts, with the time embedding took, so a re-run only embeds what
changed.

Writes evals/results/embedder-bakeoff.json and .md.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.embedding_models import LOCAL_MODELS
from app.local_embedder import TransformersEmbedder
from app.models import Chunk, Document
from app.retrieval import bm25_search, rrf_fuse, search_text
from gold import GoldItem, load_gold
from metrics import Hit, ItemResult, recall_at, score_item
from sqlalchemy import select

EVALS = Path(__file__).resolve().parent
CACHE = EVALS / ".cache"
RESULTS = EVALS / "results"
TOP_K = 10
CANDIDATES = 50  # how deep each path searches before fusion (as evals/run.py)


class Row(NamedTuple):
    id: int
    doc_id: str
    text: str
    section_path: list[str]


async def load(items: list[GoldItem]) -> tuple[list[Row], dict[str, list[int]]]:
    """Every stored chunk in a stable order, and each question's BM25 ranking."""
    engine = create_engine(get_settings())
    async with create_sessionmaker(engine)() as session:
        rows = (
            await session.execute(
                select(Chunk.id, Document.filename, Chunk.text, Chunk.section_path)
                .join(Document, Document.id == Chunk.document_id)
                .order_by(Document.filename, Chunk.seq)
            )
        ).all()
        bm25 = {
            item.id: await bm25_search(session, item.search_query, CANDIDATES) for item in items
        }
    await engine.dispose()
    return [Row(i, Path(name).stem, text, list(path)) for i, name, text, path in rows], bm25


def embed_corpus(
    embedder: TransformersEmbedder, texts: list[str]
) -> tuple[np.ndarray, float | None]:
    """Chunk vectors and the seconds embedding took (None if not recorded)."""
    key = hashlib.sha256("\x00".join([embedder.name, *texts]).encode()).hexdigest()[:16]
    path = CACHE / f"{embedder.name.replace('/', '--')}-{key}.npy"
    timing = path.with_suffix(".json")
    if path.exists():
        seconds = json.loads(timing.read_text())["seconds"] if timing.exists() else None
        return np.load(path), seconds
    started = time.perf_counter()
    vectors = np.asarray(embedder.embed_sync(texts), dtype=np.float32)
    seconds = time.perf_counter() - started
    CACHE.mkdir(exist_ok=True)
    np.save(path, vectors)
    timing.write_text(json.dumps({"seconds": seconds}))
    return vectors, seconds


def run(
    embedder: TransformersEmbedder,
    chunks: list[Row],
    items: list[GoldItem],
    bm25: dict[str, list[int]],
    with_path: bool,
) -> dict[str, Any]:
    texts = [search_text(c.section_path, c.text) if with_path else c.text for c in chunks]
    tokens = embedder.count_tokens(texts)
    vectors, embed_seconds = embed_corpus(embedder, texts)
    hits = {c.id: Hit(doc_id=c.doc_id, text=c.text) for c in chunks}

    embedder.embed_sync([embedder.model.query_prefix + "warm-up"])
    latencies: list[float] = []
    vector_results: list[ItemResult] = []
    fused_results: list[ItemResult] = []
    for item in items:
        started = time.perf_counter()
        query = np.asarray(
            embedder.embed_sync([embedder.model.query_prefix + item.search_query])[0]
        )
        latencies.append((time.perf_counter() - started) * 1000)
        ranked = [chunks[i].id for i in np.argsort(-(vectors @ query))[:CANDIDATES]]
        fused = [f.chunk_id for f in rrf_fuse({"vector": ranked, "bm25": bm25[item.id]})]
        vector_results.append(score_item(item, [hits[i] for i in ranked[:TOP_K]]))
        fused_results.append(score_item(item, [hits[i] for i in fused[:TOP_K]]))

    return {
        "model": embedder.name,
        "revision": embedder.model.revision,
        "dimensions": embedder.dimensions,
        "indexed_text": "section path + text" if with_path else "text",
        "vector": scores(vector_results),
        "fused": scores(fused_results),
        "chunks": len(texts),
        "chunks_truncated": sum(t > embedder.model.max_tokens for t in tokens),
        "tokens_median": statistics.median(tokens),
        "embed_seconds": round(embed_seconds, 1) if embed_seconds else None,
        "embed_ms_per_chunk": round(embed_seconds * 1000 / len(texts), 1)
        if embed_seconds
        else None,
        "query_ms_median": round(statistics.median(latencies), 1),
    }


def scores(results: list[ItemResult]) -> dict[str, Any]:
    tags = sorted({tag for r in results for tag in r.tags})
    return {
        "recall@5": round(recall_at(results, 5), 3),
        "recall@10": round(recall_at(results, 10), 3),
        "by_tag": {
            tag: round(recall_at([r for r in results if tag in r.tags], 10), 3) for tag in tags
        },
        # Questions with an expected passage outside the top 10.
        "misses@10": [r.id for r in results if r.recall_at(TOP_K) < 1],
    }


def bm25_only(items: list[GoldItem], chunks: list[Row], bm25: dict[str, list[int]]) -> Any:
    hits = {c.id: Hit(doc_id=c.doc_id, text=c.text) for c in chunks}
    return scores([score_item(i, [hits[c] for c in bm25[i.id][:TOP_K]]) for i in items])


def write(runs: list[dict[str, Any]], bm25: dict[str, Any], questions: int) -> None:
    RESULTS.mkdir(exist_ok=True)
    summary = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "questions": questions,
        "candidates_per_path": CANDIDATES,
        "bm25": bm25,
        "runs": runs,
    }
    (RESULTS / "embedder-bakeoff.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [
        "# Embedding model bake-off",
        "",
        (
            f"Generated by `evals/embedder_bakeoff.py`: {questions} answerable gold questions, "
            f"top {TOP_K}. *vector* is the model alone (exact search); *fused* adds the "
            f"database's BM25 ranking by reciprocal rank fusion ({CANDIDATES} candidates "
            f"per path). BM25 alone: recall@5 {bm25['recall@5']}, "
            f"recall@10 {bm25['recall@10']}."
        ),
        "",
        (
            "| model | indexed text | vector @5 | vector @10 | fused @5 | fused @10 "
            "| embed ms/chunk | query ms | chunks truncated |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        *(
            f"| {r['model']} | {r['indexed_text']} | {r['vector']['recall@5']} "
            f"| {r['vector']['recall@10']} | {r['fused']['recall@5']} | {r['fused']['recall@10']} "
            f"| {r['embed_ms_per_chunk']} | {r['query_ms_median']} | {r['chunks_truncated']} |"
            for r in runs
        ),
        "",
        "Fused, missed at 10 (a question with an expected passage outside the top 10):",
        "",
        *(
            f"- {r['model']}, {r['indexed_text']}: {', '.join(r['fused']['misses@10'])}"
            for r in runs
        ),
        "",
    ]
    (RESULTS / "embedder-bakeoff.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    items = [item for item in load_gold() if item.answerable]
    chunks, bm25 = asyncio.run(load(items))
    print(f"{len(chunks)} chunks, {len(items)} answerable questions")
    runs = []
    for key in ("bge-small", "qwen3-0.6b"):
        embedder = TransformersEmbedder(LOCAL_MODELS[key], batch_size=16)
        for with_path in (False, True):
            result = run(embedder, chunks, items, bm25, with_path)
            runs.append(result)
            print(
                f"{result['model']}, {result['indexed_text']}: "
                f"vector {result['vector']['recall@10']}, fused {result['fused']['recall@10']}",
                flush=True,
            )
    write(runs, bm25_only(items, chunks, bm25), len(items))


if __name__ == "__main__":
    main()
