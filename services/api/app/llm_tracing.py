"""One span per model call: model, tokens, cost, time to first token.

Wraps any LLMProvider, so the fake is traced exactly like the real one and
tests can check the spans. Attribute names follow OpenInference, the
conventions Phoenix reads, so its trace view shows each call as an LLM step
with its prompt, reply, token counts and cost. They are set as plain
attributes: no extra dependency.

Prompts hold document text and the user's question. That is fine for a local
Phoenix; a shared deployment should drop `input`/`output` values (README).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from app.llm import Completion, CompletionRequest, LLMError, LLMProvider, StreamEnd, StreamEvent
from app.pricing import cost_usd

tracer = trace.get_tracer(__name__)


class TracedLLMProvider:
    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    @property
    def name(self) -> str:
        return self._inner.name

    async def complete(self, request: CompletionRequest) -> Completion:
        with tracer.start_as_current_span(f"llm.{request.role}") as span:
            _record_request(span, self.name, request)
            try:
                completion = await self._inner.complete(request)
            except Exception as error:
                _record_error(span, error)
                raise
            _record_completion(span, completion)
            return completion

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        # Not "current": a span made current inside an async generator would be
        # detached in whatever context closes the generator, which OTel rejects.
        span = tracer.start_span(f"llm.{request.role}")
        _record_request(span, self.name, request)
        started = time.perf_counter()
        first_token = True
        try:
            async for event in self._inner.stream(request):
                if first_token:
                    first_token = False
                    span.set_attribute(
                        "llm.time_to_first_token_ms", (time.perf_counter() - started) * 1000
                    )
                if isinstance(event, StreamEnd):
                    _record_completion(span, event.completion)
                yield event
        except Exception as error:
            _record_error(span, error)
            raise
        except (asyncio.CancelledError, GeneratorExit):
            span.set_attribute("llm.cancelled", True)  # the listener went away mid-answer
            raise
        finally:
            span.end()


def _record_request(span: Span, provider: str, request: CompletionRequest) -> None:
    span.set_attributes(
        {
            "openinference.span.kind": "LLM",
            "llm.provider": provider,
            "llm.role": request.role.value,
            "llm.invocation_parameters": json.dumps(
                {
                    "max_tokens": request.max_tokens,
                    "output_schema": request.output.name if request.output else None,
                }
            ),
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": request.system,
        }
    )
    for i, message in enumerate(request.messages, 1):
        span.set_attribute(f"llm.input_messages.{i}.message.role", message.role)
        span.set_attribute(f"llm.input_messages.{i}.message.content", message.content)
    if request.messages:
        span.set_attribute("input.value", request.messages[-1].content)


def _record_completion(span: Span, completion: Completion) -> None:
    usage = completion.usage
    span.set_attributes(
        {
            "llm.model_name": completion.model,
            "llm.token_count.prompt": usage.input_tokens,
            "llm.token_count.completion": usage.output_tokens,
            "llm.token_count.total": usage.input_tokens + usage.output_tokens,
            "llm.token_count.prompt_details.cache_read": usage.cached_input_tokens,
            "llm.token_count.prompt_details.cache_write": usage.cache_write_input_tokens,
            "llm.token_count.completion_details.reasoning": usage.reasoning_tokens,
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": completion.text,
            "output.value": completion.text,
            "llm.stop_reason": completion.stop_reason or "",
        }
    )
    cost = cost_usd(completion.model, usage)
    if cost is not None:
        span.set_attribute("llm.cost.total", cost)


def _record_error(span: Span, error: Exception) -> None:
    if isinstance(error, LLMError):
        span.set_attribute("error.type", error.kind)
        span.set_status(Status(StatusCode.ERROR, error.message))
    else:
        span.set_attribute("error.type", type(error).__name__)
        span.set_status(Status(StatusCode.ERROR, str(error)))
