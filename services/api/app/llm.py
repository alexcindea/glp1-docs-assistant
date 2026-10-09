"""The single interface every model call goes through.

Feature code asks for a model *role* (answer, rewrite, judge) and never imports
a vendor SDK: which model serves a role, and how hard it reasons, is set in
`app.providers`. The real implementation is `app.openai_provider`; unit tests
and CI use the fake below.
"""

from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel


class ModelRole(StrEnum):
    ANSWER = "answer"  # grounded answers (strong model)
    REWRITE = "rewrite"  # follow-up question -> standalone query (small, fast model)
    JUDGE = "judge"  # eval judge (strong model)


# How long a reasoning model thinks before answering: more is slower and costs
# more output tokens. Not every model accepts every level.
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    """One model call. No temperature: current reasoning models don't take one."""

    role: ModelRole
    system: str  # instructions only; document text goes in `messages` as quoted data
    messages: list[Message]
    # Caps everything the model generates, reasoning included.
    max_tokens: int = 1024


class Usage(BaseModel):
    """Tokens one call used, as the provider bills them."""

    input_tokens: int  # all of them, including the two cache counts below
    output_tokens: int  # all of them, including reasoning
    # Prompt caching bills these at different rates, so cost needs them apart.
    cached_input_tokens: int = 0  # read from the cache
    cache_write_input_tokens: int = 0  # written to it
    reasoning_tokens: int = 0  # billed as output, never shown


class Completion(BaseModel):
    text: str
    model: str  # what the provider says answered, e.g. a dated snapshot
    usage: Usage
    stop_reason: str | None = None  # "completed", or why it stopped early


class LLMError(Exception):
    """A model call failed. `kind` says what a caller can do about it; `message`
    is safe to show users, `detail` (the provider's own words) is for the logs.
    Neither holds the prompt or a key."""

    def __init__(
        self,
        kind: Literal["unavailable", "rate_limited", "auth", "bad_request"],
        message: str,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail


class TextDelta(BaseModel):
    kind: Literal["text_delta"] = "text_delta"
    text: str


class StreamEnd(BaseModel):
    kind: Literal["end"] = "end"
    completion: Completion


StreamEvent = TextDelta | StreamEnd


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def complete(self, request: CompletionRequest) -> Completion: ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        """Text deltas as they arrive, then exactly one StreamEnd."""
        ...


Responder = Callable[[CompletionRequest], str]


def _echo(request: CompletionRequest) -> str:
    last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    return f"[fake {request.role}] {last_user}"


def _count_tokens(text: str) -> int:
    return len(text.split())


class FakeLLMProvider:
    """Deterministic stand-in: no network, no key, no cost.

    Replies come from `responder` (default: echo the last user message). Every
    request is recorded in `requests` so tests can assert on prompts.
    """

    name = "fake"

    def __init__(self, responder: Responder = _echo) -> None:
        self._responder = responder
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> Completion:
        self.requests.append(request)
        text = self._responder(request)
        prompt = " ".join([request.system, *(m.content for m in request.messages)])
        return Completion(
            text=text,
            model=f"fake-{request.role}",
            usage=Usage(input_tokens=_count_tokens(prompt), output_tokens=_count_tokens(text)),
            stop_reason="completed",
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        completion = await self.complete(request)
        words = completion.text.split(" ")
        for i, word in enumerate(words):
            yield TextDelta(text=word if i == 0 else f" {word}")
        yield StreamEnd(completion=completion)
