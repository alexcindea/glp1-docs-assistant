"""Reranker trial: does a cross-encoder over the fused candidates find the gold passage sooner?

Usage (from the repo root, with the dev database holding the ingested corpus):

    uv run python evals/reranker_trial.py

Every answerable gold question is searched as evals/run.py searches it (vector
and BM25, fused by RRF), keeping the top CANDIDATES fused chunks. Each local
cross-encoder in MODELS then scores (question, chunk) pairs, and the chunks are
re-ordered by that score. Recall@5 and @10 are compared with fusion alone, with
the time the reranker adds per question (CPU, one batch of CANDIDATES pairs).

The bar set before running (phase 7 plan): keep a reranker only if recall@5
gains at least 2 of the 31 questions and it adds under 0.5 s per question.
This is a trial, not a feature: nothing in the app uses a reranker.

Models are pinned to a revision; all are permissively licensed (checked on
their model cards): MiniLM-L6 Apache-2.0, mxbai-rerank-xsmall Apache-2.0,
bge-reranker-base MIT. Writes evals/results/reranker-trial.json and .md.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.models import Chunk, Document
from app.retrieval import CANDIDATES as PER_PATH
from app.retrieval import bm25_search, fuse, vector_search
from gold import GoldItem, load_gold
from metrics import Hit, ItemResult, recall_at, score_item
from run import stored_model
from sqlalchemy import select
from transformers import AutoModelForSequenceClassification, AutoTokenizer

RESULTS = Path(__file__).resolve().parent / "results"
CANDIDATES = 30  # fused chunks the reranker re-orders
MAX_TOKENS = 512  # per (question, chunk) pair


@dataclass(frozen=True)
class Reranker:
    name: str
    revision: str
    licence: str


MODELS = (
    Reranker("cross-encoder/ms-marco-MiniLM-L6-v2", "233902d25c44", "Apache-2.0"),
    Reranker("mixedbread-ai/mxbai-rerank-xsmall-v1", "b5c6e9da73ab", "Apache-2.0"),
    Reranker("BAAI/bge-reranker-base", "2cfc18c9415c", "MIT"),
)


class CrossEncoder:
    def __init__(self, model: Reranker) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(model.name, revision=model.revision)
        self.model = AutoModelForSequenceClassification.from_pretrained(
            model.name, revision=model.revision
        ).eval()

    @torch.no_grad()
    def scores(self, question: str, passages: list[str]) -> list[float]:
        batch = self.tokenizer(
            [question] * len(passages),
            passages,
            padding=True,
            truncation="only_second",
            max_length=MAX_TOKENS,
            return_tensors="pt",
        )
        logits = self.model(**batch).logits
        return [float(x) for x in logits[:, 0]]  # one relevance logit per pair


async def fused_candidates(items: list[GoldItem]) -> tuple[list[list[int]], dict[int, Any]]:
    """The top CANDIDATES fused chunk ids per question, and every chunk's text."""
    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            embedder = await stored_model(session)
            chunks = {
                chunk_id: (Hit(doc_id=Path(filename).stem, text=text), search)
                for chunk_id, filename, text, search in await session.execute(
                    select(Chunk.id, Document.filename, Chunk.text, Chunk.search_text).join(
                        Document, Document.id == Chunk.document_id
                    )
                )
            }
            ranked = []
            for item in items:
                query = await embedder.embed_query(item.search_query)
                vector = await vector_search(session, query, embedder.name, PER_PATH)
                bm25 = await bm25_search(session, item.search_query, PER_PATH)
                fused = await fuse(session, {"vector": vector, "bm25": bm25}, CANDIDATES)
                ranked.append([f.chunk_id for f in fused])
    finally:
        await engine.dispose()
    return ranked, chunks


def summarise(results: list[ItemResult], ms: list[float] | None) -> dict[str, Any]:
    return {
        "recall@5": round(recall_at(results, 5), 3),
        "recall@10": round(recall_at(results, 10), 3),
        "found@5": sum(r.recall_at(5) == 1.0 for r in results),  # every gold passage in top 5
        "ms_median": round(statistics.median(ms), 1) if ms else None,
        "ms_p95": round(sorted(ms)[int(len(ms) * 0.95)], 1) if ms else None,
    }


def main() -> None:
    items = [item for item in load_gold() if item.answerable]
    ranked, chunks = asyncio.run(fused_candidates(items))

    def score(order: list[int], k: int) -> list[ItemResult]:
        return [score_item(item, [chunks[i][0] for i in ids[:k]]) for item, ids in order]

    pairs = list(zip(items, ranked, strict=True))
    report: dict[str, Any] = {
        "fused (no reranker)": summarise(score(pairs, CANDIDATES), None),
        f"fused, recall@{CANDIDATES} (ceiling)": {
            "recall@any": round(recall_at(score(pairs, CANDIDATES), CANDIDATES), 3)
        },
    }
    for model in MODELS:
        encoder = CrossEncoder(model)
        encoder.scores("warm-up", ["warm-up"])  # first call is slow; not timed
        reordered, times = [], []
        for item, ids in pairs:
            started = time.perf_counter()
            relevance = encoder.scores(item.search_query, [chunks[i][1] for i in ids])
            times.append((time.perf_counter() - started) * 1000)
            order = [i for _, i in sorted(zip(relevance, ids, strict=True), reverse=True)]
            reordered.append((item, order))
        report[model.name] = summarise(score(reordered, CANDIDATES), times)
        print(model.name, report[model.name], flush=True)
    write(report, len(items))


def write(report: dict[str, Any], questions: int) -> None:
    document = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "questions": questions,
        "candidates": CANDIDATES,
        "models": [vars(m) for m in MODELS],
        "results": report,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "reranker-trial.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    base, ceiling, *models = report.items()
    lines = [
        "# Reranker trial",
        "",
        (
            f"Generated by `evals/reranker_trial.py`: {questions} answerable gold questions, "
            f"the top {CANDIDATES} fused chunks re-ordered by each cross-encoder (CPU, one "
            "batch per question). The bar: recall@5 up by at least 2 questions, under "
            f"0.5 s added. Recall within the top {CANDIDATES} fused chunks, the most any "
            f"reranker of them could reach: {ceiling[1]['recall@any']}."
        ),
        "",
        "| ranking | recall@5 | recall@10 | questions found in top 5 | added ms, median | p95 |",
        "|---|---:|---:|---:|---:|---:|",
        *(
            f"| {name} | {r['recall@5']} | {r['recall@10']} | {r['found@5']} | "
            f"{r['ms_median'] or '-'} | {r['ms_p95'] or '-'} |"
            for name, r in [base, *models]
        ),
        "",
    ]
    (RESULTS / "reranker-trial.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
