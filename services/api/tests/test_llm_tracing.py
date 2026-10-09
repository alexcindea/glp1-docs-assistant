"""Every model call is one span with the model, tokens, cost and time to first token."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import llm_tracing
from app.llm import CompletionRequest, FakeLLMProvider, LLMError, Message, ModelRole
from app.llm_tracing import TracedLLMProvider


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm_tracing, "tracer", provider.get_tracer(__name__))
    return exporter


def _request() -> CompletionRequest:
    return CompletionRequest(
        role=ModelRole.ANSWER,
        system="Answer from the passages.",
        messages=[Message(role="user", content="What is the dose?")],
    )


def _only(exporter: InMemorySpanExporter) -> ReadableSpan:
    (span,) = exporter.get_finished_spans()
    return span


@pytest.mark.anyio
async def test_a_call_is_one_llm_span_with_tokens_and_cost(spans: InMemorySpanExporter) -> None:
    llm = TracedLLMProvider(FakeLLMProvider(responder=lambda _: "2 mg [1]"))

    await llm.complete(_request())

    span = _only(spans)
    attributes = dict(span.attributes or {})
    assert span.name == "llm.answer"
    assert attributes["openinference.span.kind"] == "LLM"
    assert attributes["llm.model_name"] == "fake-answer"
    assert attributes["llm.token_count.completion"] == 3  # the fake counts words
    assert attributes["llm.cost.total"] == 0
    assert attributes["llm.input_messages.0.message.content"] == "Answer from the passages."
    assert attributes["output.value"] == "2 mg [1]"


@pytest.mark.anyio
async def test_a_stream_is_one_span_with_time_to_first_token(spans: InMemorySpanExporter) -> None:
    llm = TracedLLMProvider(FakeLLMProvider(responder=lambda _: "two words"))

    events = [event async for event in llm.stream(_request())]

    span = _only(spans)
    attributes = dict(span.attributes or {})
    assert len(events) == 3
    assert attributes["llm.time_to_first_token_ms"] >= 0  # type: ignore[operator]
    assert attributes["llm.token_count.completion"] == 2


@pytest.mark.anyio
async def test_a_failed_call_is_an_error_span(spans: InMemorySpanExporter) -> None:
    def fail(_: CompletionRequest) -> str:
        raise LLMError("rate_limited", "OpenAI rate limit or quota reached")

    llm = TracedLLMProvider(FakeLLMProvider(responder=fail))

    with pytest.raises(LLMError):
        _ = [event async for event in llm.stream(_request())]

    span = _only(spans)
    assert span.status.status_code == trace.StatusCode.ERROR
    assert (span.attributes or {})["error.type"] == "rate_limited"
