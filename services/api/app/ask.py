"""Ask one question from the terminal, through the same pipeline the API uses.

    cd services/api; uv run python -m app.ask "What is the maximum dose of Ozempic?"
    uv run python -m app.ask --document <uuid> "..."   # only in these documents
    uv run python -m app.ask --previous "What's the maximum dose of Ozempic?" "And Mounjaro?"

Uses the settings in .env: LLM_PROVIDER=openai and OPENAI_API_KEY for real
answers, EMBEDDER set to the model the corpus was embedded with. Prints the
passages found, streams the answer, then its citations (or the refusal and the
closest passages), tokens, cost and timings. With OTEL_TRACES_ENDPOINT set,
the question is one trace in Phoenix.
"""

import argparse
import asyncio
import io
import sys
import uuid

from opentelemetry import trace

from app.answer_prompt import pages
from app.answering import DeltaEvent, DoneEvent, ErrorEvent, SourcesEvent, answer_question
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.local_embedder import TransformersEmbedder
from app.observability import configure_logging, create_tracer_provider
from app.passages import SearchHit
from app.providers import build_embedder, build_llm_provider
from app.rewrite import Turn


def _where(hit: SearchHit) -> str:
    section = " > ".join(hit.chunk.section_path) or "(no section)"
    return f"{hit.filename} p.{pages(hit)} | {section}"


async def ask(question: str, previous: list[str], document_ids: list[uuid.UUID] | None) -> int:
    settings = get_settings()
    embedder = build_embedder(settings)
    if isinstance(embedder, TransformersEmbedder):
        await asyncio.to_thread(embedder.load)
    else:
        print(
            f"note: EMBEDDER={settings.embedder}: vector search only sees chunks it embedded",
            file=sys.stderr,
        )
    llm = build_llm_provider(settings)
    engine = create_engine(settings)
    status = 0
    try:
        async with create_sessionmaker(engine)() as session:
            async for event in answer_question(
                session,
                embedder,
                llm,
                question,
                history=[Turn(question=q) for q in previous],
                document_ids=document_ids,
                rewrite=settings.query_rewrite,
            ):
                match event:
                    case SourcesEvent(query=query, passages=passages):
                        if query != question:
                            print(f"searched for: {query}")
                        print(f"\n{len(passages)} passages:")
                        for n, hit in enumerate(passages, 1):
                            print(f"  [{n}] {_where(hit)}")
                        print()
                    case DeltaEvent(text=text):
                        print(text, end="", flush=True)
                    case DoneEvent() as done:
                        _print_done(done)
                    case ErrorEvent(code=code, message=message):
                        print(f"\nerror ({code}): {message}", file=sys.stderr)
                        status = 1
    finally:
        await engine.dispose()
    return status


def _print_done(done: DoneEvent) -> None:
    if done.refusal:
        print(f"\nREFUSED ({done.refusal.reason}): {done.refusal.message}")
        print("Closest passages:")
        for hit in done.refusal.closest:
            print(f"  - {_where(hit)}")
    else:
        print("\n\nCitations:")
        for citation in done.citations:
            section = " > ".join(citation.section_path)
            start, end = citation.page_start, citation.page_end
            where = f"p.{start}" if start == end else f"p.{start}-{end}"
            print(f"  [{citation.n}] {citation.filename} {where} | {section}")
    usage, timings = done.usage, done.timings
    cost = "unknown" if usage.cost_usd is None else f"${usage.cost_usd:.4f}"
    first = "-" if timings.first_token_ms is None else f"{timings.first_token_ms / 1000:.1f} s"
    print(
        f"\n{done.model or 'no model call'} | {usage.input_tokens} in "
        f"({usage.cached_input_tokens} cached), {usage.output_tokens} out "
        f"({usage.reasoning_tokens} reasoning) | {cost}\n"
        f"retrieval {timings.retrieval_ms:.0f} ms, first token {first}, "
        f"total {timings.total_ms / 1000:.1f} s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the documents one question.")
    parser.add_argument("question")
    parser.add_argument("--document", type=uuid.UUID, action="append", dest="documents")
    parser.add_argument(
        "--previous", action="append", default=[], help="an earlier question (repeatable)"
    )
    args = parser.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # PDFs hold "≥", "µ"; Windows pipes are cp1252

    settings = get_settings()
    configure_logging(settings)
    provider = create_tracer_provider(settings)
    if provider:
        trace.set_tracer_provider(provider)
    try:
        status = asyncio.run(ask(args.question, args.previous, args.documents))
    finally:
        if provider:
            provider.shutdown()  # send the trace before exiting
    sys.exit(status)


if __name__ == "__main__":
    main()
