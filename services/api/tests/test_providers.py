"""The fake LLM provider and embedder behave predictably, so other tests can rely on them."""

import math

import pytest

from app.config import Settings
from app.embeddings import FakeEmbedder
from app.llm import (
    CompletionRequest,
    FakeLLMProvider,
    Message,
    ModelRole,
    StreamEnd,
    TextDelta,
)
from app.providers import build_embedder, build_llm_provider


def _request(question: str) -> CompletionRequest:
    return CompletionRequest(
        role=ModelRole.ANSWER,
        system="Answer from the sources.",
        messages=[Message(role="user", content=question)],
    )


@pytest.mark.anyio
async def test_fake_llm_uses_the_responder_and_records_requests() -> None:
    llm = FakeLLMProvider(responder=lambda request: "scripted reply")

    completion = await llm.complete(_request("What is the dose?"))

    assert completion.text == "scripted reply"
    assert completion.model == "fake-answer"
    assert completion.usage.output_tokens == 2
    assert completion.usage.input_tokens > 0
    assert llm.requests[0].messages[0].content == "What is the dose?"


@pytest.mark.anyio
async def test_fake_llm_stream_rebuilds_the_full_text_then_ends_once() -> None:
    llm = FakeLLMProvider(responder=lambda request: "one two three")

    events = [event async for event in llm.stream(_request("q"))]

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert "".join(deltas) == "one two three"
    assert len(ends) == 1
    assert events[-1] is ends[0]
    assert ends[0].completion.text == "one two three"


@pytest.mark.anyio
async def test_fake_embedder_is_deterministic_normalised_and_sized() -> None:
    embedder = FakeEmbedder(dimensions=64)

    first, again = await embedder.embed_documents(["store below 25 C", "store below 25 C"])

    assert len(first) == 64
    assert first == again
    assert math.isclose(math.fsum(v * v for v in first), 1.0)


@pytest.mark.anyio
async def test_fake_embedder_puts_texts_sharing_words_closer() -> None:
    embedder = FakeEmbedder()

    query = await embedder.embed_query("storage temperature for the pen")
    related, unrelated = await embedder.embed_documents(
        ["Store the pen at a cool temperature.", "The committee met on Tuesday."]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return math.fsum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, related) > cosine(query, unrelated)


def test_factory_builds_fakes_with_configured_dimensions() -> None:
    settings = Settings(embedding_dimensions=1024)

    assert build_llm_provider(settings).name == "fake"
    embedder = build_embedder(settings)
    assert embedder.name == "fake"
    assert embedder.dimensions == 1024
