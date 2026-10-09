"""The single interface every model call goes through.

Feature code asks for a model *role* (answer, rewrite, judge) and never imports
a vendor SDK. The real OpenAI implementation lands with grounded answers;
until then, and always in unit tests and CI, the fake below is used.
"""

from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel


class ModelRole(StrEnum):
    ANSWER = "answer"  # grounded answers (strong model)
    REWRITE = "rewrite"  # follow-up question -> standalone query (small, fast model)
    JUDGE = "judge"  # eval judge (strong model)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    role: ModelRole
    system: str
    messages: list[Message]
    max_tokens: int = 1024
    temperature: float = 0.0


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    # Prompt caching bills these at different rates, so cost needs them apart.
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class Completion(BaseModel):
    text: str
    model: str
    usage: Usage
    stop_reason: str | None = None


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
            stop_reason="end_turn",
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        completion = await self.complete(request)
        words = completion.text.split(" ")
        for i, word in enumerate(words):
            yield TextDelta(text=word if i == 0 else f" {word}")
        yield StreamEnd(completion=completion)
