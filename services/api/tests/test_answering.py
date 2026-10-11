"""The answer pipeline against a real Postgres, with the fake embedder and a scripted model."""

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import answering, llm_tracing, retrieval
from app.answering import DeltaEvent, DoneEvent, ErrorEvent, Event, SourcesEvent, answer_question
from app.llm import (
    Completion,
    CompletionRequest,
    FakeLLMProvider,
    LLMError,
    LLMProvider,
    Responder,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Usage,
)
from app.llm_tracing import TracedLLMProvider
from tests.factories import (
    DOSE,
    EMBEDDER,
    add_corpus,
    cites_the_dose,
)


class ChunkedLLM:
    """Streams exactly the given pieces, so tests choose where deltas split."""

    name = "chunked"

    def __init__(
        self,
        chunks: Sequence[str],
        *,
        stop_reason: str = "completed",
        hang_after: int | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.hang_after = hang_after  # stop and wait forever after this many chunks
        self.fail_with = fail_with
        self.closed = False

    async def complete(self, request: CompletionRequest) -> Completion:
        raise NotImplementedError

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        try:
            for i, chunk in enumerate(self.chunks, 1):
                yield TextDelta(text=chunk)
                if i == self.hang_after:
                    await asyncio.Event().wait()
            if self.fail_with:
                raise self.fail_with
            yield StreamEnd(
                completion=Completion(
                    text="".join(self.chunks),
                    model="fake-answer",
                    usage=Usage(input_tokens=10, output_tokens=5),
                    stop_reason=self.stop_reason,
                )
            )
        finally:
            self.closed = True


async def ask_llm(
    sessions: async_sessionmaker[AsyncSession], llm: LLMProvider, question: str
) -> list[Event]:
    async with sessions() as session:
        return [event async for event in answer_question(session, EMBEDDER, llm, question)]


async def ask(
    sessions: async_sessionmaker[AsyncSession], responder: Responder, question: str
) -> tuple[list[Event], FakeLLMProvider]:
    llm = FakeLLMProvider(responder=responder)
    return await ask_llm(sessions, llm, question), llm


def done_of(events: list[Event]) -> DoneEvent:
    assert isinstance(events[-1], DoneEvent)
    assert sum(isinstance(e, DoneEvent | ErrorEvent) for e in events) == 1
    return events[-1]


def streamed(events: list[Event]) -> str:
    return "".join(e.text for e in events if isinstance(e, DeltaEvent))


@pytest.mark.anyio
async def test_an_answer_streams_then_cites_the_passage_with_its_page_and_box(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)

    events, _ = await ask(sessions, cites_the_dose, "What is the maximum dose of axolotlin?")

    assert isinstance(events[0], SourcesEvent)
    assert len(events[0].passages) == 3
    done = done_of(events)
    assert done.refusal is None
    assert streamed(events) == done.answer
    assert done.answer.startswith("The maximum dose is 2 mg once weekly [")
    (citation,) = done.citations
    assert events[0].passages[citation.n - 1].chunk.text == DOSE
    assert (citation.page_start, citation.page_end) == (4, 4)
    assert (citation.char_start, citation.char_end) == (0, len(DOSE))
    (span,) = citation.spans
    assert (span.page, span.x0, span.y1) == (4, 0.1, 0.25)
    assert done.model == "fake-answer"
    assert done.usage.input_tokens > 0
    assert done.usage.cost_usd == 0
    assert done.timings.first_token_ms is not None
    assert done.timings.retrieval_ms <= done.timings.total_ms


@pytest.mark.anyio
async def test_not_found_is_a_refusal_that_never_streams_and_offers_the_closest_passages(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)

    events, _ = await ask(sessions, lambda _: "NOT_FOUND", "What does axolotlin cost?")

    done = done_of(events)
    assert streamed(events) == ""
    assert done.answer == ""
    assert done.citations == []
    assert done.refusal is not None
    assert done.refusal.reason == "not_found"
    assert "don't answer" in done.refusal.message
    assert 1 <= len(done.refusal.closest) <= answering.CLOSEST
    assert done.timings.first_token_ms is None


@pytest.mark.anyio
async def test_medical_advice_is_refused_as_out_of_scope(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)

    events, _ = await ask(sessions, lambda _: "OUT_OF_SCOPE", "Should I take axolotlin?")

    refusal = done_of(events).refusal
    assert refusal is not None
    assert refusal.reason == "out_of_scope"


@pytest.mark.anyio
async def test_an_answer_that_cites_nothing_is_refused_after_streaming(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)

    events, _ = await ask(sessions, lambda _: "It is 2 mg [7].", "What is the maximum dose?")

    done = done_of(events)
    assert streamed(events) == "It is 2 mg [7]."  # shown, then replaced by the refusal
    assert done.refusal is not None
    assert done.refusal.reason == "no_citations"
    assert done.answer == ""


@pytest.mark.anyio
async def test_with_nothing_to_search_the_model_is_never_called(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    events, llm = await ask(sessions, cites_the_dose, "What is the maximum dose?")

    done = done_of(events)
    assert done.refusal is not None
    assert done.refusal.reason == "no_passages"
    assert done.refusal.closest == []
    assert done.model is None
    assert llm.requests == []


@pytest.mark.anyio
async def test_a_failed_model_call_ends_with_a_typed_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)

    def fail(_: CompletionRequest) -> str:
        raise LLMError("rate_limited", "OpenAI rate limit or quota reached")

    events, _ = await ask(sessions, fail, "What is the maximum dose?")

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "llm_rate_limited"
    assert not any(isinstance(e, DoneEvent) for e in events)


@pytest.mark.anyio
async def test_a_question_is_one_trace_with_retrieval_and_the_model_call_inside(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_corpus(sessions)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    for module in (answering, retrieval, llm_tracing):
        monkeypatch.setattr(module, "tracer", provider.get_tracer(module.__name__))
    llm = TracedLLMProvider(FakeLLMProvider(responder=cites_the_dose))

    async with sessions() as session:
        _ = [e async for e in answer_question(session, EMBEDDER, llm, "maximum dose?")]

    spans = {span.name: span for span in exporter.get_finished_spans()}
    root = spans["answer"]
    assert root.parent is None
    assert {spans[name].context.trace_id for name in spans} == {root.context.trace_id}
    assert spans["retrieve"].parent == root.context
    assert spans["llm.answer"].parent == root.context
    attributes = dict(root.attributes or {})
    assert attributes["answer.refused"] is False
    assert attributes["answer.citations"] == 1
    assert attributes["answer.cost_usd"] == 0


@pytest.mark.anyio
async def test_characters_held_back_while_undecided_are_not_lost(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)
    # "N", "No", "NoT" could each still become NOT_FOUND, so they are held back
    llm = ChunkedLLM(["N", "o", "T", ", the dose is 2 mg [1]."])

    events = await ask_llm(sessions, llm, "What is the maximum dose?")

    assert streamed(events) == "NoT, the dose is 2 mg [1]."
    assert done_of(events).answer == "NoT, the dose is 2 mg [1]."


@pytest.mark.anyio
@pytest.mark.parametrize(
    "chunks", [["NOT", "_FO", "UND"], ["**", "NOT_", "FOUND**"], ["NOT_FOUND"]]
)
async def test_a_sentinel_split_across_deltas_never_streams(
    sessions: async_sessionmaker[AsyncSession], chunks: list[str]
) -> None:
    await add_corpus(sessions)

    events = await ask_llm(sessions, ChunkedLLM(chunks), "What does axolotlin cost?")

    assert streamed(events) == ""
    refusal = done_of(events).refusal
    assert refusal is not None
    assert refusal.reason == "not_found"


@pytest.mark.anyio
async def test_a_reply_cut_off_by_the_token_cap_is_an_error_not_a_refusal(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)
    llm = ChunkedLLM(["The dose is 2 mg [1"], stop_reason="max_output_tokens")

    events = await ask_llm(sessions, llm, "What is the maximum dose?")

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "llm_incomplete"
    assert not any(isinstance(e, DoneEvent) for e in events)


@pytest.mark.anyio
async def test_an_unexpected_failure_still_ends_with_one_typed_error(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_corpus(sessions)
    llm = ChunkedLLM(["The dose"], fail_with=RuntimeError("database went away"))

    events = await ask_llm(sessions, llm, "What is the maximum dose?")

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "internal_error"
    assert "database" not in events[-1].message  # details stay in the logs
    assert sum(isinstance(e, DoneEvent | ErrorEvent) for e in events) == 1


@pytest.mark.anyio
async def test_a_listener_leaving_mid_answer_stops_the_model_call(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_corpus(sessions)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm_tracing, "tracer", provider.get_tracer(__name__))
    inner = ChunkedLLM(["The dose", " is"], hang_after=1)

    async with sessions() as session:
        events = answer_question(session, EMBEDDER, TracedLLMProvider(inner), "dose?")
        assert isinstance(await anext(events), SourcesEvent)
        assert isinstance(await anext(events), DeltaEvent)
        await events.aclose()  # e.g. the browser tab closed

    assert inner.closed
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "llm.answer"]
    assert (span.attributes or {})["llm.cancelled"] is True
