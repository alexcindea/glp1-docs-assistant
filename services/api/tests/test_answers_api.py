"""POST /answers: server-sent events through the real app and database, with a scripted model."""

import asyncio
import dataclasses
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import select

from app.answering import AnswerEvent, DeltaEvent, DoneEvent, ErrorEvent, SourcesEvent
from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.factory import create_app
from app.llm import CompletionRequest, FakeLLMProvider, LLMError, ModelRole, Responder
from app.models import AnswerFeedback, AnswerRecord
from tests.test_search_api import _ready_document

EVENTS: TypeAdapter[AnswerEvent] = TypeAdapter(AnswerEvent)
_PASSAGE = re.compile(r'<passage id="(\d+)"[^>]*>\n(.*?)\n</passage>', re.DOTALL)


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(database_url=migrated_database_url)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


def use_llm(client: TestClient, responder: Responder) -> FakeLLMProvider:
    llm = FakeLLMProvider(responder=responder)
    app: Any = client.app
    app.state.resources = dataclasses.replace(app.state.resources, llm=llm)
    return llm


def cite(containing: str) -> Responder:
    """Answers by citing the passage holding `containing`; rewrites to 'antibodies'."""

    def respond(request: CompletionRequest) -> str:
        if request.role == ModelRole.REWRITE:
            return "percentage of patients who developed antibodies"
        for number, text in _PASSAGE.findall(request.messages[-1].content):
            if containing in text:
                return f"Forty-four percent developed antibodies [{number}]."
        return "NOT_FOUND"

    return respond


def ask(client: TestClient, body: dict[str, Any]) -> list[Any]:
    with client.stream("POST", "/answers", json=body) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = response.read().decode()
    data = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    return [EVENTS.validate_python(json.loads(item)) for item in data]


def saved(database_url: str, answer_id: uuid.UUID) -> AnswerRecord:
    async def load() -> AnswerRecord:
        engine = create_engine(Settings(database_url=database_url))
        async with create_sessionmaker(engine)() as session:
            record = await session.scalar(select(AnswerRecord).where(AnswerRecord.id == answer_id))
        await engine.dispose()
        assert record is not None
        return record

    return asyncio.run(load())


def test_an_answer_streams_as_events_and_is_saved_with_its_citations(
    client: TestClient, migrated_database_url: str
) -> None:
    document_id = _ready_document(client, migrated_database_url)
    use_llm(client, cite("antibodies"))

    events = ask(client, {"question": "How many developed antibodies?"})

    assert isinstance(events[0], SourcesEvent)
    assert events[0].query == "How many developed antibodies?"  # no history: as asked
    assert any(isinstance(e, DeltaEvent) for e in events)
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.refusal is None
    (citation,) = done.citations
    assert str(citation.document_id) == document_id
    assert (citation.page_start, citation.spans[0].page) == (2, 2)
    assert done.answer_id is not None
    record = saved(migrated_database_url, done.answer_id)
    assert record.answer == done.answer
    assert record.citations[0]["spans"][0]["page"] == 2
    assert record.refusal_reason is None
    assert record.search_query == "How many developed antibodies?"


def test_a_refusal_is_streamed_and_saved_with_its_reason(
    client: TestClient, migrated_database_url: str
) -> None:
    _ready_document(client, migrated_database_url)
    use_llm(client, lambda _: "NOT_FOUND")

    events = ask(client, {"question": "What does it cost?"})

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.refusal is not None
    assert done.refusal.closest
    assert done.answer_id is not None
    assert saved(migrated_database_url, done.answer_id).refusal_reason == "not_found"


def test_a_follow_up_is_searched_as_its_standalone_question(
    client: TestClient, migrated_database_url: str
) -> None:
    _ready_document(client, migrated_database_url)
    llm = use_llm(client, cite("antibodies"))

    events = ask(
        client,
        {
            "question": "And how many of them?",
            "history": [{"question": "Did patients develop antibodies?", "answer": "Some did."}],
        },
    )

    rewrite, answer = llm.requests
    assert rewrite.role == ModelRole.REWRITE
    assert "Did patients develop antibodies?" in rewrite.messages[0].content
    assert isinstance(events[0], SourcesEvent)
    assert events[0].query == "percentage of patients who developed antibodies"
    assert "The question in full: percentage of patients" in answer.messages[0].content
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.citations


def test_a_model_failure_ends_the_stream_with_a_typed_error(
    client: TestClient, migrated_database_url: str
) -> None:
    _ready_document(client, migrated_database_url)

    def fail(_: CompletionRequest) -> str:
        raise LLMError("rate_limited", "OpenAI rate limit or quota reached")

    use_llm(client, fail)

    events = ask(client, {"question": "How many developed antibodies?"})

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "llm_rate_limited"


def test_unknown_documents_are_a_404_before_any_streaming(client: TestClient) -> None:
    unknown = str(uuid.uuid4())

    response = client.post("/answers", json={"question": "dose?", "document_ids": [unknown]})

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_documents"


def test_an_empty_question_is_rejected(client: TestClient) -> None:
    response = client.post("/answers", json={"question": "   "})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_the_event_types_are_in_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    stream = schema["paths"]["/answers"]["post"]["responses"]["200"]["content"]
    event_schema = stream["text/event-stream"]["itemSchema"]["properties"]["data"]
    assert "contentSchema" in event_schema
    components = schema["components"]["schemas"]
    for name in ("SourcesEvent", "DeltaEvent", "DoneEvent", "ErrorEvent", "Citation"):
        assert name in components


def test_errors_of_the_stream_are_documented_as_json(client: TestClient) -> None:
    responses = client.get("/openapi.json").json()["paths"]["/answers"]["post"]["responses"]

    for status in ("404", "422", "500"):
        assert list(responses[status]["content"]) == ["application/json"]


def feedback_rows(database_url: str) -> list[tuple[uuid.UUID, str, str | None]]:
    async def load() -> list[tuple[uuid.UUID, str, str | None]]:
        engine = create_engine(Settings(database_url=database_url))
        async with create_sessionmaker(engine)() as session:
            rows = await session.execute(
                select(AnswerFeedback.answer_id, AnswerFeedback.rating, AnswerFeedback.comment)
            )
            result = [(a, r, c) for a, r, c in rows]
        await engine.dispose()
        return result

    return asyncio.run(load())


def test_feedback_is_saved_and_giving_it_again_replaces_it(
    client: TestClient, migrated_database_url: str
) -> None:
    _ready_document(client, migrated_database_url)
    use_llm(client, cite("antibodies"))
    done = ask(client, {"question": "How many developed antibodies?"})[-1]
    assert isinstance(done, DoneEvent)
    assert done.answer_id is not None

    first = client.post(f"/answers/{done.answer_id}/feedback", json={"rating": "up"})
    second = client.post(
        f"/answers/{done.answer_id}/feedback",
        json={"rating": "down", "comment": "  Cites the wrong page.  "},
    )

    assert first.status_code == second.status_code == 200
    assert second.json() == {
        "answer_id": str(done.answer_id),
        "rating": "down",
        "comment": "Cites the wrong page.",
    }
    assert feedback_rows(migrated_database_url) == [
        (done.answer_id, "down", "Cites the wrong page.")
    ]


def test_feedback_on_an_unknown_answer_is_a_404_and_a_bad_rating_a_422(
    client: TestClient,
) -> None:
    unknown = uuid.uuid4()

    assert client.post(f"/answers/{unknown}/feedback", json={"rating": "up"}).status_code == 404
    bad = client.post(f"/answers/{unknown}/feedback", json={"rating": "meh"})
    assert bad.status_code == 422
