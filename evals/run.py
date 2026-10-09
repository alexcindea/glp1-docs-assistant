"""Retrieval eval: recall@5 and recall@10 on the gold set, per search path.

Usage (from the repo root, with the dev database holding the ingested corpus):
    uv run python evals/run.py

Runs every answerable gold question through the same search functions the API
uses (app.retrieval), against the database, and scores the top 10 of each path
with evals/metrics.py: vector, BM25, and the two fused by reciprocal rank fusion,
as the API returns them. Unanswerable questions belong to the refusal metrics
(phase 5). The embedding model is the one the stored vectors were made with; the
script stops if the database holds chunks without vectors or from another model.

Writes evals/results/retrieval.json and .md.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.embedding_models import LOCAL_MODELS
from app.local_embedder import TransformersEmbedder
from app.models import Chunk, Document
from app.retrieval import CANDIDATES, bm25_search, fuse, vector_search
from gold import GoldItem, load_gold
from metrics import Hit, ItemResult, recall_at, score_item
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

RESULTS = Path(__file__).resolve().parent / "results"
TOP_K = 10
PATHS = ("vector", "bm25", "fused")


async def evaluate(items: list[GoldItem]) -> tuple[dict[str, Any], str]:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session:
            embedder = await stored_model(session)
            chunks = {
                chunk_id: Hit(doc_id=Path(filename).stem, text=text)
                for chunk_id, filename, text in await session.execute(
                    select(Chunk.id, Document.filename, Chunk.text).join(
                        Document, Document.id == Chunk.document_id
                    )
                )
            }
            await embedder.embed_query("warm-up")  # loads the model; not timed
            results: dict[str, list[ItemResult]] = {path: [] for path in PATHS}
            timings: dict[str, list[float]] = {step: [] for step in ("embed", *PATHS)}
            for item in items:
                clock = time.perf_counter()
                query = await embedder.embed_query(item.search_query)
                clock = lap(timings["embed"], clock)
                vector = await vector_search(session, query, embedder.name, CANDIDATES)
                clock = lap(timings["vector"], clock)
                bm25 = await bm25_search(session, item.search_query, CANDIDATES)
                lap(timings["bm25"], clock)
                fused = [
                    f.chunk_id for f in await fuse(session, {"vector": vector, "bm25": bm25}, TOP_K)
                ]
                # A whole search, as app.retrieval.retrieve does it: embed, search, fuse.
                timings["fused"].append(
                    timings["embed"][-1] + timings["vector"][-1] + timings["bm25"][-1]
                )
                for path, ids in (("vector", vector), ("bm25", bm25), ("fused", fused)):
                    results[path].append(score_item(item, [chunks[i] for i in ids[:TOP_K]]))
    finally:
        await engine.dispose()
    report = {path: summarise(results[path], timings[path]) for path in PATHS}
    report["fused"]["embed_ms_median"] = round(statistics.median(timings["embed"]), 1)
    return report, embedder.name


async def stored_model(session: AsyncSession) -> TransformersEmbedder:
    """The local model the stored vectors were made with (every chunk, one model)."""
    rows = await session.execute(
        select(Chunk.embedding_model, func.count()).group_by(Chunk.embedding_model)
    )
    models: dict[str | None, int] = {name: count for name, count in rows}  # not dict(Result)
    by_name = {TransformersEmbedder(model).name: model for model in LOCAL_MODELS.values()}
    (name,) = models if len(models) == 1 else (None,)
    if name not in by_name:
        raise SystemExit(
            f"Chunks per embedding model: {models}. Every chunk needs a vector from one "
            "local model: run the worker with EMBEDDER set to it and re-chunk the corpus "
            "(scripts/ingest_corpus.py --rechunk)."
        )
    return TransformersEmbedder(by_name[name])


def lap(times: list[float], started: float) -> float:
    now = time.perf_counter()
    times.append((now - started) * 1000)
    return now


def summarise(results: list[ItemResult], latencies: list[float]) -> dict[str, Any]:
    tags = sorted({tag for r in results for tag in r.tags})
    return {
        "recall@5": round(recall_at(results, 5), 3),
        "recall@10": round(recall_at(results, 10), 3),
        "ms_median": round(statistics.median(latencies), 1),
        "ms_p95": round(sorted(latencies)[int(len(latencies) * 0.95)], 1),
        "by_tag": {
            tag: {
                "n": len(group := [r for r in results if tag in r.tags]),
                "recall@10": round(recall_at(group, 10), 3),
            }
            for tag in tags
        },
        "ranks": {r.id: list(r.ranks) for r in results},  # per expected passage; null = not found
    }


def write(report: dict[str, Any], model: str, questions: int) -> None:
    RESULTS.mkdir(exist_ok=True)
    summary = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "questions": questions,
        "embedding_model": model,
        "candidates_per_path": CANDIDATES,
        "paths": report,
    }
    (RESULTS / "retrieval.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    tags = sorted({tag for path in report.values() for tag in path["by_tag"]})
    lines = [
        "# Retrieval eval",
        "",
        (
            f"Generated by `evals/run.py`: {questions} answerable gold questions, top {TOP_K}, "
            f"embedding model {model}, {CANDIDATES} candidates per path before fusion. "
            "Latency is on the dev machine (CPU); the fused row is a whole search: embed "
            f"the question ({report['fused']['embed_ms_median']} ms median), both searches, "
            "fuse."
        ),
        "",
        "| path | recall@5 | recall@10 | median ms | p95 ms |",
        "|---|---:|---:|---:|---:|",
        *(
            f"| {name} | {p['recall@5']} | {p['recall@10']} | {p['ms_median']} | {p['ms_p95']} |"
            for name, p in report.items()
        ),
        "",
        "recall@10 by question tag:",
        "",
        f"| tag | n | {' | '.join(report)} |",
        f"|---|---:|{'---:|' * len(report)}",
        *(
            f"| {tag} | {next(iter(report.values()))['by_tag'][tag]['n']} | "
            + " | ".join(str(p["by_tag"][tag]["recall@10"]) for p in report.values())
            + " |"
            for tag in tags
        ),
        "",
    ]
    (RESULTS / "retrieval.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    items = [item for item in load_gold() if item.answerable]
    report, model = asyncio.run(evaluate(items))
    write(report, model, len(items))
    for name, path in report.items():
        print(
            f"{name:8} recall@5 {path['recall@5']:.3f}  recall@10 {path['recall@10']:.3f}  "
            f"median {path['ms_median']} ms"
        )


if __name__ == "__main__":
    main()
