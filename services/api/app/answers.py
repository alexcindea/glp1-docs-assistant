"""POST /answers: a grounded answer, streamed as server-sent events.

Each SSE message carries one event of app.answering as JSON, told apart by its
`kind`: `sources`, then `delta`s, then `done` or `error`. The OpenAPI schema
describes them, so the web app's types are generated like every other type.

The finished answer is saved (table `answers`): `done` is sent once it is,
carrying its id for feedback. If saving fails, the answer is still delivered,
without an id. A client that disconnects cancels the work, model call included.
"""

import time
import uuid
from collections.abc import AsyncIterable
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse
from opentelemetry import trace
from pydantic import BaseModel, Field, StringConstraints

from app.answering import AnswerEvent, DoneEvent, ErrorEvent, SourcesEvent, answer_question
from app.db import DATABASE_ERRORS
from app.errors import stream_error_responses
from app.models import AnswerRecord
from app.passages import check_documents
from app.rewrite import Turn
from app.state import AppStateDep, SessionDep

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/answers", tags=["answers"])


class AnswerRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    # Earlier turns of the conversation, oldest first: what makes "And for Mounjaro?" a question.
    history: list[Turn] = Field(default_factory=list, max_length=10)
    # Only these documents; null or omitted answers from all of them.
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)


async def _known_documents(request: AnswerRequest, session: SessionDep) -> None:
    """Runs before the stream starts, so an unknown id is a plain 404, not a stream event."""
    if request.document_ids is not None:
        await check_documents(session, request.document_ids)


@router.post(
    "",
    response_class=EventSourceResponse,
    responses=stream_error_responses(404),
    dependencies=[Depends(_known_documents)],
)
async def answer(
    request: AnswerRequest, state: AppStateDep, session: SessionDep
) -> AsyncIterable[AnswerEvent]:
    started = time.perf_counter()
    trace_id = _current_trace_id()
    query = request.question
    ending: DoneEvent | ErrorEvent | None = None
    events = answer_question(
        session,
        state.embedder,
        state.llm,
        request.question,
        history=request.history,
        document_ids=request.document_ids,
        rewrite=state.settings.query_rewrite,
    )
    try:
        async for event in events:
            if isinstance(event, DoneEvent | ErrorEvent):
                ending = event  # sent once saved
                continue
            if isinstance(event, SourcesEvent):
                query = event.query
            yield event
    finally:
        await events.aclose()
    if ending is None:  # answer_question always ends with one
        return

    record = _record(request, query, ending, trace_id, (time.perf_counter() - started) * 1000)
    try:
        session.add(record)
        await session.commit()
    except DATABASE_ERRORS:
        logger.exception("answer.not_saved")
        yield ending
        return
    if isinstance(ending, DoneEvent):
        ending = ending.model_copy(update={"answer_id": record.id})
    yield ending


def _record(
    request: AnswerRequest,
    query: str,
    ending: DoneEvent | ErrorEvent,
    trace_id: str | None,
    elapsed_ms: float,
) -> AnswerRecord:
    common: dict[str, Any] = {
        "id": uuid.uuid4(),
        "question": request.question,
        "history": [turn.model_dump() for turn in request.history],
        "search_query": query,
        "document_ids": request.document_ids,
        "trace_id": trace_id,
    }
    if isinstance(ending, ErrorEvent):
        return AnswerRecord(
            **common,
            answer="",
            citations=[],
            error_code=ending.code,
            input_tokens=0,
            output_tokens=0,
            total_ms=elapsed_ms,
        )
    return AnswerRecord(
        **common,
        answer=ending.answer,
        citations=[citation.model_dump(mode="json") for citation in ending.citations],
        refusal_reason=ending.refusal.reason if ending.refusal else None,
        model=ending.model,
        input_tokens=ending.usage.input_tokens,
        output_tokens=ending.usage.output_tokens,
        cost_usd=ending.usage.cost_usd,
        first_token_ms=ending.timings.first_token_ms,
        total_ms=ending.timings.total_ms,
    )


def _current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
