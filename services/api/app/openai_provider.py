"""LLMProvider on OpenAI's Responses API. The only module that imports the SDK.

Each role maps to a model and a reasoning effort (`RoleModel`), so feature code
never names a model. Responses are not stored at OpenAI (`store=False`): every
call carries its own context, and nothing about a question outlives it there.

Prompt caching is off. By default OpenAI writes each prompt to its cache, which
costs 25% more than plain input, and pays off only when a later prompt starts
the same way. Ours start with the system prompt (too short to cache), then
passages that differ per question: measured, every write was wasted.

SDK errors become `LLMError`, so callers handle one exception type, with a
message fit for users; OpenAI's own wording goes to `detail`, for the logs. The
SDK retries connection errors, 429s and 5xx itself (twice, with backoff) before
we see them.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import openai
from openai.types.responses import Response, ResponseInputParam
from openai.types.responses.response_create_params import PromptCacheOptions

from app.config import Settings
from app.llm import (
    Completion,
    CompletionRequest,
    LLMError,
    ModelRole,
    ReasoningEffort,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Usage,
)

# "explicit" with no breakpoints marked: nothing is cached.
NO_CACHE_WRITES: PromptCacheOptions = {"mode": "explicit"}


@dataclass(frozen=True)
class RoleModel:
    model: str
    effort: ReasoningEffort


class OpenAIProvider:
    name = "openai"

    def __init__(self, client: openai.AsyncOpenAI, models: Mapping[ModelRole, RoleModel]) -> None:
        self._client = client
        self._models = dict(models)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIProvider":
        key = settings.openai_api_key.get_secret_value().strip() if settings.openai_api_key else ""
        if not key:  # unset, or left blank as in .env.example
            raise ValueError("LLM_PROVIDER=openai needs OPENAI_API_KEY (see .env.example)")
        client = openai.AsyncOpenAI(api_key=key, timeout=settings.openai_timeout_seconds)
        return cls(
            client,
            {
                ModelRole.ANSWER: RoleModel(settings.llm_answer_model, settings.llm_answer_effort),
                ModelRole.REWRITE: RoleModel(
                    settings.llm_rewrite_model, settings.llm_rewrite_effort
                ),
                ModelRole.JUDGE: RoleModel(settings.llm_judge_model, settings.llm_judge_effort),
            },
        )

    async def complete(self, request: CompletionRequest) -> Completion:
        try:
            role = self._models[request.role]
            response = await self._client.responses.create(
                model=role.model,
                instructions=request.system,
                input=_input(request),
                max_output_tokens=request.max_tokens,
                reasoning={"effort": role.effort},
                store=False,
                prompt_cache_options=NO_CACHE_WRITES,
            )
        except openai.OpenAIError as error:
            raise _llm_error(error) from error
        return _completion(response)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        try:
            role = self._models[request.role]
            async with await self._client.responses.create(
                model=role.model,
                instructions=request.system,
                input=_input(request),
                max_output_tokens=request.max_tokens,
                reasoning={"effort": role.effort},
                store=False,
                prompt_cache_options=NO_CACHE_WRITES,
                stream=True,
            ) as events:
                async for event in events:
                    match event.type:
                        case "response.output_text.delta":
                            yield TextDelta(text=event.delta)
                        case "response.completed" | "response.incomplete":
                            yield StreamEnd(completion=_completion(event.response))
                            return
                        case "response.failed":
                            error = event.response.error
                            raise _stream_error(
                                error.code if error else None,
                                error.message if error else "the response failed",
                            )
                        case "error":
                            raise _stream_error(event.code, event.message)
        except openai.OpenAIError as error:
            raise _llm_error(error) from error
        raise LLMError("unavailable", "the stream ended before the response completed")


def _input(request: CompletionRequest) -> ResponseInputParam:
    return [{"role": message.role, "content": message.content} for message in request.messages]


def _completion(response: Response) -> Completion:
    usage = response.usage
    stop_reason: str
    if response.status == "incomplete":
        details = response.incomplete_details
        stop_reason = details.reason if details and details.reason else "incomplete"
    else:
        stop_reason = response.status or "completed"
    return Completion(
        text=response.output_text,
        model=response.model,
        usage=Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.input_tokens_details.cached_tokens,
            cache_write_input_tokens=usage.input_tokens_details.cache_write_tokens,
            reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
        )
        if usage
        else Usage(input_tokens=0, output_tokens=0),
        stop_reason=stop_reason,
    )


def _llm_error(error: openai.OpenAIError) -> LLMError:
    match error:
        case openai.AuthenticationError() | openai.PermissionDeniedError():
            return LLMError("auth", "OpenAI rejected the API key (check OPENAI_API_KEY)")
        case openai.RateLimitError():
            return LLMError("rate_limited", "OpenAI rate limit or quota reached")
        case openai.BadRequestError() | openai.NotFoundError():
            return LLMError("bad_request", "OpenAI refused the request", detail=error.message)
        case openai.APIConnectionError():
            return LLMError("unavailable", "could not reach OpenAI")
        case openai.APIStatusError():
            return LLMError("unavailable", "OpenAI is not answering", detail=error.message)
        case _:
            return LLMError("unavailable", "OpenAI is not answering", detail=repr(error))


def _stream_error(code: str | None, message: str) -> LLMError:
    """An error OpenAI reported inside a stream, after the HTTP request succeeded."""
    if code and "rate_limit" in code:
        return LLMError("rate_limited", "OpenAI rate limit or quota reached", detail=message)
    return LLMError("unavailable", "OpenAI failed while answering", detail=f"{code}: {message}")
