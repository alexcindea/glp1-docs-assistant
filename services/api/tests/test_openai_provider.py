"""The OpenAI adapter, against a fake HTTP endpoint: no network, no key, no cost."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import openai
import pytest
from pydantic import BaseModel

from app.llm import (
    CompletionRequest,
    LLMError,
    Message,
    ModelRole,
    StreamEnd,
    TextDelta,
    output_schema,
)
from app.openai_provider import OpenAIProvider, RoleModel

MODELS = {
    ModelRole.ANSWER: RoleModel("gpt-6.1-sol", "low"),
    ModelRole.REWRITE: RoleModel("gpt-6-luna", "none"),
    ModelRole.JUDGE: RoleModel("gpt-6-astra", "medium"),
}


def _response(text: str, status: str = "completed", **extra: Any) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "gpt-6.1-sol-2026-09-01",
        "status": status,
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 1200,
            "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 0},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 20},
            "total_tokens": 1250,
        },
        **extra,
    }


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def _provider(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[OpenAIProvider, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    client = openai.AsyncOpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(record)),
    )
    return OpenAIProvider(client, MODELS), sent


def _request(role: ModelRole = ModelRole.ANSWER) -> CompletionRequest:
    return CompletionRequest(
        role=role,
        system="Answer from the passages.",
        messages=[Message(role="user", content="What is the dose?")],
        max_tokens=2000,
    )


@pytest.mark.anyio
async def test_complete_sends_the_roles_model_and_reads_usage() -> None:
    provider, sent = _provider(lambda _: httpx2.Response(200, json=_response("2 mg [1]")))

    completion = await provider.complete(_request(ModelRole.REWRITE))

    (body,) = sent
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"] == {"effort": "none"}
    assert body["instructions"] == "Answer from the passages."
    assert body["input"] == [{"role": "user", "content": "What is the dose?"}]
    assert body["max_output_tokens"] == 2000
    assert body["store"] is False
    assert body["prompt_cache_options"] == {"mode": "explicit"}  # no paid cache writes
    assert "temperature" not in body
    assert "text" not in body  # free text unless a schema is asked for
    assert completion.text == "2 mg [1]"
    assert completion.model == "gpt-6.1-sol-2026-09-01"
    assert completion.stop_reason == "completed"
    usage = completion.usage
    assert (usage.input_tokens, usage.cached_input_tokens) == (1200, 1000)
    assert (usage.output_tokens, usage.reasoning_tokens) == (50, 20)


class Verdict(BaseModel):
    correct: bool
    note: str | None = None


@pytest.mark.anyio
async def test_a_schema_request_asks_for_strict_structured_output() -> None:
    provider, sent = _provider(
        lambda _: httpx2.Response(200, json=_response('{"correct": true, "note": null}'))
    )
    request = _request(ModelRole.JUDGE).model_copy(update={"output": output_schema(Verdict)})

    completion = await provider.complete(request)

    (body,) = sent
    assert body["text"] == {
        "format": {
            "type": "json_schema",
            "name": "Verdict",
            "schema": output_schema(Verdict).json_schema,
            "strict": True,
        }
    }
    assert Verdict.model_validate_json(completion.text) == Verdict(correct=True)


@pytest.mark.anyio
async def test_stream_yields_text_deltas_then_one_end() -> None:
    body = _sse(
        {"type": "response.created", "sequence_number": 0, "response": _response("", "queued")},
        *(
            {
                "type": "response.output_text.delta",
                "sequence_number": i,
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": piece,
                "logprobs": [],
            }
            for i, piece in enumerate(["2 mg", " once", " weekly [1]."], 1)
        ),
        {"type": "response.completed", "sequence_number": 9, "response": _response("2 mg [1].")},
    )
    provider, sent = _provider(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )

    events = [event async for event in provider.stream(_request())]

    assert sent[0]["stream"] is True
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "2 mg once weekly [1]."
    assert isinstance(events[-1], StreamEnd)
    assert sum(isinstance(e, StreamEnd) for e in events) == 1
    assert events[-1].completion.usage.input_tokens == 1200


@pytest.mark.anyio
async def test_a_streamed_schema_request_asks_for_structured_output_too() -> None:
    body = _sse({"type": "response.completed", "sequence_number": 1, "response": _response("{}")})
    provider, sent = _provider(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )
    request = _request(ModelRole.JUDGE).model_copy(update={"output": output_schema(Verdict)})

    _ = [event async for event in provider.stream(request)]

    assert sent[0]["text"]["format"]["name"] == "Verdict"
    assert sent[0]["text"]["format"]["strict"] is True


@pytest.mark.anyio
async def test_a_response_cut_off_by_the_token_cap_says_so() -> None:
    cut = _response("2 mg", "incomplete", incomplete_details={"reason": "max_output_tokens"})
    provider, _ = _provider(lambda _: httpx2.Response(200, json=cut))

    completion = await provider.complete(_request())

    assert completion.stop_reason == "max_output_tokens"


@pytest.mark.anyio
async def test_a_failed_stream_raises_llm_error() -> None:
    failed = _response("", "failed", error={"code": "server_error", "message": "model overloaded"})
    body = _sse({"type": "response.failed", "sequence_number": 1, "response": failed})
    provider, _ = _provider(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )

    with pytest.raises(LLMError) as caught:
        _ = [event async for event in provider.stream(_request())]
    assert caught.value.kind == "unavailable"
    assert "model overloaded" in (caught.value.detail or "")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (429, "rate_limited"), (400, "bad_request"), (503, "unavailable")],
)
async def test_http_errors_become_typed_llm_errors(status: int, kind: str) -> None:
    error = {"error": {"message": "nope", "type": "x", "code": None, "param": None}}
    provider, _ = _provider(lambda _: httpx2.Response(status, json=error))

    with pytest.raises(LLMError) as caught:
        await provider.complete(_request())

    assert caught.value.kind == kind
    assert "test-key" not in caught.value.message


@pytest.mark.anyio
async def test_openai_wording_goes_to_the_log_detail_not_the_user_message() -> None:
    error = {"error": {"message": "Invalid 'max_output_tokens'", "type": "x", "code": None}}
    provider, _ = _provider(lambda _: httpx2.Response(400, json=error))

    with pytest.raises(LLMError) as caught:
        await provider.complete(_request())

    assert caught.value.message == "OpenAI refused the request"
    assert "Invalid 'max_output_tokens'" in (caught.value.detail or "")


@pytest.mark.anyio
async def test_a_rate_limit_reported_mid_stream_keeps_its_kind() -> None:
    body = _sse(
        {"type": "error", "sequence_number": 1, "code": "rate_limit_exceeded", "message": "slow"}
    )
    provider, _ = _provider(
        lambda _: httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"})
    )

    with pytest.raises(LLMError) as caught:
        _ = [event async for event in provider.stream(_request())]

    assert caught.value.kind == "rate_limited"
