"""Grounded answers: retrieve passages, stream the model's answer, check its citations.

One question produces a stream of events:

    sources  the search query and the passages the model sees, numbered from 1
    delta    a piece of answer text, as it arrives (any number of these)
    done     the checked answer and its citations, or a refusal; tokens, cost, timings
    error    instead of done: the model call failed or was cut off, or something
             else went wrong (typed `code`, a message fit for users)

Refusal is decided in three places (see app.citations for the details):

1. Retrieval found nothing: refuse without calling the model ("no_passages").
2. The model replies with a sentinel (NOT_FOUND, OUT_OF_SCOPE). The first few
   characters are held back until they can't be one, so a refusal never
   streams as answer text.
3. The finished reply cites no valid passage ("no_citations"). Its text has
   streamed by then; `done` says it is refused, and clients replace it.

A refusal always carries the closest passages, so the user can look themselves.

Before searching, a follow-up question is rewritten into a standalone query
(app.rewrite), and with QUERY_REWRITE=always every question is. The answer
model sees the question as asked and, when it differs, the rewritten one.

The question's work runs in its own task and reaches the caller through a
queue. Spans opened in that task are opened and closed without a `yield` in
between, which OpenTelemetry needs; an async generator that yields inside a
span breaks it.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Annotated, Literal

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.answer_prompt import build_answer_request, page_range
from app.citations import CheckedAnswer, RefusalReason, check_answer, classify_start
from app.documents import SpanOut
from app.embeddings import Embedder
from app.llm import Completion, LLMError, LLMProvider, StreamEnd
from app.passages import SearchHit, load_hits
from app.pricing import cost_usd
from app.retrieval import retrieve
from app.rewrite import Turn, standalone_query

RewritePolicy = Literal["follow_ups", "always"]

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

PASSAGES = 10  # what the model sees: fused recall@10 is 0.79, @5 0.68
CLOSEST = 3  # passages offered with a refusal

REFUSAL_MESSAGES: dict[RefusalReason, str] = {
    "not_found": "The documents don't answer this question.",
    "no_passages": "The documents don't answer this question.",
    "no_citations": "I couldn't back an answer with a passage from the documents.",
    "out_of_scope": (
        "I can only report what these documents say, and can't give personal medical "
        "advice or discuss how I work."
    ),
}


class Citation(BaseModel):
    """Passage `n` of the sources, and where to highlight it."""

    n: int
    document_id: uuid.UUID
    filename: str
    chunk_id: int
    section_path: list[str]
    page_start: int
    page_end: int  # differs from page_start when the passage crosses a page break
    # The cited range of the chunk's text, and the spans covering it. Today that
    # is the whole chunk; narrowing it to a sentence would not change this shape.
    char_start: int
    char_end: int
    spans: list[SpanOut]


class Refusal(BaseModel):
    reason: RefusalReason
    message: str
    closest: list[SearchHit]


class AnswerUsage(BaseModel):
    """Every model call for the question, summed."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = 0.0  # None when a model's price is unknown


class Timings(BaseModel):
    rewrite_ms: float | None  # None when the question was searched as asked
    retrieval_ms: float
    first_token_ms: float | None  # from the question to the first answer text shown
    total_ms: float


class SourcesEvent(BaseModel):
    kind: Literal["sources"] = "sources"
    query: str  # what was searched for
    passages: list[SearchHit]  # passage n is passages[n - 1]


class DeltaEvent(BaseModel):
    kind: Literal["delta"] = "delta"
    text: str


class DoneEvent(BaseModel):
    kind: Literal["done"] = "done"
    answer: str  # with normalised [n] markers; "" when refused
    citations: list[Citation]
    refusal: Refusal | None
    model: str | None  # the answer model; None when it wasn't called
    usage: AnswerUsage
    timings: Timings
    answer_id: uuid.UUID | None = None  # set once the answer is saved (POST /answers)


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    code: str  # "llm_unavailable", "llm_rate_limited", "llm_incomplete", "internal_error", ...
    message: str


Event = SourcesEvent | DeltaEvent | DoneEvent | ErrorEvent
AnswerEvent = Annotated[Event, Field(discriminator="kind")]  # for parsing and the schema

INTERNAL_ERROR = ErrorEvent(
    code="internal_error", message="Unexpected server error. The details are in the API logs."
)
INCOMPLETE = ErrorEvent(
    code="llm_incomplete",
    message="The answer was cut off before it finished. Try a narrower question.",
)


async def answer_question(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLMProvider,
    question: str,
    *,
    history: Sequence[Turn] = (),
    document_ids: Sequence[uuid.UUID] | None = None,
    rewrite: RewritePolicy = "follow_ups",
) -> AsyncGenerator[Event]:
    """The events for one question, ending with exactly one `done` or `error`.

    Closing the generator early (the client left) cancels the work, model call included.
    """
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def run() -> None:
        try:
            await _answer(
                session, embedder, llm, question, history, document_ids, rewrite, queue.put
            )
        except Exception:
            # Database, embedder or a bug: the client still gets a typed ending.
            logger.exception("answer.failed")
            await queue.put(INTERNAL_ERROR)
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())
    try:
        while (event := await queue.get()) is not None:
            yield event
        await task
    finally:
        if not task.done():  # the caller stopped listening (client went away)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _answer(
    session: AsyncSession,
    embedder: Embedder,
    llm: LLMProvider,
    question: str,
    history: Sequence[Turn],
    document_ids: Sequence[uuid.UUID] | None,
    rewrite: RewritePolicy,
    emit: Callable[[Event], Awaitable[None]],
) -> None:
    started = time.perf_counter()

    def since_start() -> float:
        return (time.perf_counter() - started) * 1000

    with tracer.start_as_current_span("answer") as span:
        span.set_attributes(
            {
                "openinference.span.kind": "CHAIN",
                "input.value": question,
                "answer.document_filter": len(document_ids) if document_ids else 0,
                "answer.history_turns": len(history),
            }
        )
        query, rewrite_ms, rewritten = question, None, None
        if history or rewrite == "always":
            query, rewritten = await standalone_query(llm, question, history)
            rewrite_ms = since_start()
        span.set_attribute("answer.search_query", query)
        searching = time.perf_counter()
        fused = await retrieve(session, embedder, query, limit=PASSAGES, document_ids=document_ids)
        passages = await load_hits(session, fused)
        retrieval_ms = (time.perf_counter() - searching) * 1000
        await emit(SourcesEvent(query=query, passages=passages))

        completion: Completion | None = None
        first_token_ms: float | None = None
        if not passages:
            checked = CheckedAnswer(text="", refusal="no_passages")
        else:
            received = ""
            start: Literal["undecided", "refusal", "answer"] = "undecided"
            try:
                request = build_answer_request(question, passages, in_full=query)
                async for event in llm.stream(request):
                    if isinstance(event, StreamEnd):
                        completion = event.completion
                        continue
                    received += event.text
                    if start == "answer":
                        await emit(DeltaEvent(text=event.text))
                    elif start == "undecided":
                        start = classify_start(received)
                        if start == "answer":
                            first_token_ms = since_start()
                            await emit(DeltaEvent(text=received))
            except LLMError as error:
                logger.warning("answer.llm_failed", kind=error.kind, detail=error.detail)
                span.set_attribute("error.type", error.kind)
                span.set_status(Status(StatusCode.ERROR, error.message))
                await emit(ErrorEvent(code=f"llm_{error.kind}", message=error.message))
                return
            if completion is None:  # the provider contract promises a StreamEnd
                raise RuntimeError(f"{llm.name} stream ended without a completion")
            if completion.stop_reason != "completed":
                # Out of tokens or filtered: neither a whole answer nor a real "not found".
                logger.warning("answer.cut_short", stop_reason=completion.stop_reason)
                span.set_attribute("error.type", "incomplete")
                span.set_status(Status(StatusCode.ERROR, completion.stop_reason))
                await emit(INCOMPLETE)
                return
            checked = check_answer(completion.text, len(passages))
            if checked.invalid:
                logger.warning("answer.invalid_citations", numbers=checked.invalid)

        timings = Timings(
            rewrite_ms=rewrite_ms,
            retrieval_ms=retrieval_ms,
            first_token_ms=first_token_ms,
            total_ms=since_start(),
        )
        calls = [c for c in (rewritten, completion) if c is not None]
        done = _done(passages, checked, completion, calls, timings)
        _record(span, done)
        await emit(done)


def _done(
    passages: list[SearchHit],
    checked: CheckedAnswer,
    completion: Completion | None,
    calls: list[Completion],
    timings: Timings,
) -> DoneEvent:
    refusal = (
        Refusal(
            reason=checked.refusal,
            message=REFUSAL_MESSAGES[checked.refusal],
            closest=passages[:CLOSEST],
        )
        if checked.refusal
        else None
    )
    return DoneEvent(
        answer="" if refusal else checked.text,
        citations=[] if refusal else [_citation(n, passages[n - 1]) for n in checked.cited],
        refusal=refusal,
        model=completion.model if completion else None,
        usage=_usage(calls),
        timings=timings,
    )


def _citation(n: int, hit: SearchHit) -> Citation:
    chunk = hit.chunk
    page_start, page_end = page_range(hit)
    return Citation(
        n=n,
        document_id=hit.document_id,
        filename=hit.filename,
        chunk_id=chunk.id,
        section_path=chunk.section_path,
        page_start=page_start,
        page_end=page_end,
        char_start=0,
        char_end=len(chunk.text),
        spans=chunk.spans,
    )


def _usage(completions: list[Completion]) -> AnswerUsage:
    usage = AnswerUsage()
    for completion in completions:
        tokens = completion.usage
        usage.input_tokens += tokens.input_tokens
        usage.cached_input_tokens += tokens.cached_input_tokens
        usage.output_tokens += tokens.output_tokens
        usage.reasoning_tokens += tokens.reasoning_tokens
        cost = cost_usd(completion.model, tokens)
        usage.cost_usd = None if cost is None or usage.cost_usd is None else usage.cost_usd + cost
    return usage


def _record(span: trace.Span, done: DoneEvent) -> None:
    span.set_attributes(
        {
            "output.value": done.answer or (done.refusal.message if done.refusal else ""),
            "answer.refused": done.refusal is not None,
            "answer.refusal_reason": done.refusal.reason if done.refusal else "",
            "answer.citations": len(done.citations),
            "answer.input_tokens": done.usage.input_tokens,
            "answer.output_tokens": done.usage.output_tokens,
            "answer.retrieval_ms": done.timings.retrieval_ms,
            "answer.total_ms": done.timings.total_ms,
        }
    )
    if done.usage.cost_usd is not None:
        span.set_attribute("answer.cost_usd", done.usage.cost_usd)
    if done.timings.first_token_ms is not None:
        span.set_attribute("answer.first_token_ms", done.timings.first_token_ms)
