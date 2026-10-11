"""Answer eval: every gold question through the real answer pipeline, scored.

Usage (from the repo root; the dev database holds the ingested corpus, and
.env sets LLM_PROVIDER=openai with OPENAI_API_KEY):

    uv run python evals/answers.py                     # both rewrite settings
    uv run python evals/answers.py --rewrite always    # one

Each question goes through app.answering.answer_question, as POST /answers
runs it, once per QUERY_REWRITE setting (follow_ups, always), and is scored
by evals/answer_metrics.py: did it answer with the gold passage cited, or
refuse when it should. Costs about 20 cents per setting with the default
models. The model is not deterministic, so a question can flip between runs.

Writes evals/results/answers.json and .md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

from answer_metrics import CitedPassage, Outcome, Score, score, summarise
from app.answering import (
    Citation,
    DoneEvent,
    ErrorEvent,
    RewritePolicy,
    SourcesEvent,
    answer_question,
)
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.embeddings import Embedder
from app.llm import LLMProvider
from app.passages import SearchHit
from app.providers import build_llm_provider
from app.rewrite import Turn
from gold import GoldItem, load_gold
from metrics import Hit
from run import stored_model
from sqlalchemy.ext.asyncio import AsyncSession

RESULTS = Path(__file__).resolve().parent / "results"


def hit(passage: SearchHit) -> Hit:
    return Hit(doc_id=Path(passage.filename).stem, text=passage.chunk.text)


def cited(citation: Citation, passage: SearchHit) -> CitedPassage:
    first, last = citation.page_start, citation.page_end
    return CitedPassage(
        n=citation.n,
        doc_id=Path(citation.filename).stem,
        pages=str(first) if first == last else f"{first}-{last}",
        section=" > ".join(citation.section_path),
        text=passage.chunk.text,
    )


async def ask(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLMProvider,
    item: GoldItem,
    rewrite: RewritePolicy,
) -> Outcome:
    sources: list[SearchHit] = []
    query = item.question
    async for event in answer_question(
        session,
        embedder,
        llm,
        item.question,
        history=[Turn(question=q) for q in item.history],
        rewrite=rewrite,
    ):
        if isinstance(event, SourcesEvent):
            sources, query = event.passages, event.query
        elif isinstance(event, ErrorEvent):
            return Outcome(
                id=item.id,
                sources=tuple(map(hit, sources)),
                cited=(),
                refusal=None,
                error=event.code,
                search_query=query,
            )
        elif isinstance(event, DoneEvent):
            return Outcome(
                id=item.id,
                sources=tuple(map(hit, sources)),
                cited=tuple(hit(sources[c.n - 1]) for c in event.citations),
                refusal=event.refusal.reason if event.refusal else None,
                error=None,
                cost_usd=event.usage.cost_usd,
                first_token_ms=event.timings.first_token_ms,
                total_ms=event.timings.total_ms,
                search_query=query,
                answer=event.answer,
                citations=tuple(cited(c, sources[c.n - 1]) for c in event.citations),
            )
    raise RuntimeError(f"{item.id}: the pipeline ended without done or error")


async def evaluate(
    items: list[GoldItem], settings_to_run: list[RewritePolicy]
) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    if settings.llm_provider != "openai":
        raise SystemExit(
            "Set LLM_PROVIDER=openai and OPENAI_API_KEY in .env: this eval needs the real model."
        )
    llm = build_llm_provider(settings)
    engine = create_engine(settings)
    report: dict[str, dict[str, Any]] = {}
    try:
        async with create_sessionmaker(engine)() as session:
            embedder = await stored_model(session)
            await embedder.embed_query("warm-up")  # loads the model; not timed
            for rewrite in settings_to_run:
                outcomes: list[Outcome] = []
                for n, item in enumerate(items, 1):
                    outcome = await ask(session, embedder, llm, item, rewrite)
                    outcomes.append(outcome)
                    verdict = score(item, outcome).verdict
                    print(f"[{rewrite}] {n}/{len(items)} {item.id} {verdict}", flush=True)
                scores = [score(item, o) for item, o in zip(items, outcomes, strict=True)]
                report[rewrite] = {
                    "summary": summarise(scores, outcomes),
                    "items": [
                        row(item, s, o) for item, s, o in zip(items, scores, outcomes, strict=True)
                    ],
                }
    finally:
        await engine.dispose()
    return report


def row(item: GoldItem, result: Score, outcome: Outcome) -> dict[str, Any]:
    return {
        "id": item.id,
        "tags": item.tags,
        "verdict": result.verdict,
        "passed": result.passed,
        "retrieved": result.retrieved,
        "citation_recall": result.citation_recall,
        "refusal": outcome.refusal,
        "error": outcome.error,
        "search_query": outcome.search_query,
        "answer": outcome.answer,
        "citations": [asdict(c) for c in outcome.citations],
        "cost_usd": outcome.cost_usd,
        "first_token_ms": None if outcome.first_token_ms is None else round(outcome.first_token_ms),
        "total_ms": None if outcome.total_ms is None else round(outcome.total_ms),
    }


def write(report: dict[str, dict[str, Any]], items: list[GoldItem]) -> None:
    settings = get_settings()
    models = {
        "answer": f"{settings.llm_answer_model} ({settings.llm_answer_effort})",
        "rewrite": f"{settings.llm_rewrite_model} ({settings.llm_rewrite_effort})",
    }
    RESULTS.mkdir(exist_ok=True)
    document = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "questions": len(items),
        "models": models,
        "by_rewrite_setting": report,
    }
    (RESULTS / "answers.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    runs = list(report)
    metrics = [
        ("pass rate (all questions)", "pass_rate"),
        ("answerable: answered citing the gold passage", "answered_with_gold_citation"),
        ("answerable: refused (false refusal)", "false_refusal_rate"),
        ("answerable: gold passage among the 10 sources", "retrieved"),
        ("answerable: failed although retrieved", "failed_although_retrieved"),
        ("not answerable: refused", "correct_refusal_rate"),
        ("cost per question, mean (USD)", "cost_usd_mean"),
        ("first text, median ms", "first_token_ms_median"),
        ("first text, p95 ms", "first_token_ms_p95"),
        ("whole answer, median ms", "total_ms_median"),
        ("whole answer, p95 ms", "total_ms_p95"),
    ]
    lines = [
        "# Answer eval",
        "",
        (
            f"Generated by `evals/answers.py`: {len(items)} gold questions through the answer "
            f"pipeline, answer model {models['answer']}, rewrite model {models['rewrite']}. "
            "A question passes when it is answered citing its gold passage, or refused when "
            "the documents don't answer it. The wording of answers is not judged here. "
            "Latency is on the dev machine (CPU embeddings) plus OpenAI's response time."
        ),
        "",
        f"| metric | {' | '.join(f'rewrite: {r}' for r in runs)} |",
        f"|---|{'---:|' * len(runs)}",
        *(
            f"| {label} | " + " | ".join(str(report[r]["summary"][key]) for r in runs) + " |"
            for label, key in metrics
        ),
        "",
        "Per question:",
        "",
        f"| id | tags | {' | '.join(runs)} |",
        f"|---|---|{'---|' * len(runs)}",
        *(
            f"| {item.id} | {', '.join(item.tags)} | "
            + " | ".join(_cell(report[r]["items"][i]) for r in runs)
            + " |"
            for i, item in enumerate(items)
        ),
        "",
    ]
    (RESULTS / "answers.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _cell(item: dict[str, Any]) -> str:
    mark = "pass" if item["passed"] else "**fail**"
    detail = item["verdict"]
    if item["verdict"] in ("false_refusal", "cited_other") and not item["retrieved"]:
        detail += ", not retrieved"
    return f"{mark}: {detail}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rewrite", choices=get_args(RewritePolicy))
    args = parser.parse_args()
    to_run: list[RewritePolicy] = [args.rewrite] if args.rewrite else list(get_args(RewritePolicy))
    items = load_gold()
    report = asyncio.run(evaluate(items, to_run))
    write(report, items)
    for rewrite, run in report.items():
        s = run["summary"]
        print(
            f"rewrite={rewrite}: pass {s['pass_rate']}, answered with gold citation "
            f"{s['answered_with_gold_citation']}, correct refusals {s['correct_refusal_rate']}, "
            f"${s['cost_usd_mean']}/question, first text {s['first_token_ms_median']} ms median"
        )


if __name__ == "__main__":
    main()
