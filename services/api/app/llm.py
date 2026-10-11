"""The single interface every model call goes through.

Feature code asks for a model *role* (answer, rewrite, judge) and never imports
a vendor SDK: which model serves a role, and how hard it reasons, is set in
`app.providers`. The real implementation is `app.openai_provider`; unit tests
and CI use the fake below.
"""

import re
from collections.abc import AsyncIterator, Callable
from enum import StrEnum
from typing import Any, Literal, Protocol

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


class OutputSchema(BaseModel):
    """Ask for a reply that is JSON matching `json_schema`, not free text.

    Built from a Pydantic model with `output_schema`; the caller validates the
    reply with that same model. Providers that support it enforce the schema
    while generating, so the reply always parses.
    """

    name: str  # letters, digits, "_" and "-"; providers show it to the model
    json_schema: dict[str, Any]


class CompletionRequest(BaseModel):
    """One model call. No temperature: current reasoning models don't take one."""

    role: ModelRole
    system: str  # instructions only; document text goes in `messages` as quoted data
    messages: list[Message]
    # Caps everything the model generates, reasoning included.
    max_tokens: int = 1024
    output: OutputSchema | None = None  # None: free text


def output_schema(model: type[BaseModel]) -> OutputSchema:
    """The strict JSON Schema for `model`, in the subset structured outputs accept.

    Strict mode needs every object closed (`additionalProperties: false`) and
    every property listed as required; a field that may be absent is written as
    `X | None` instead. Titles and defaults are dropped: they are not part of
    that subset, and a default would make a field look optional. A reference
    with keywords beside it (a nested model with a description) is inlined,
    since strict mode allows nothing next to `$ref`.

    Shapes strict mode can't express raise ValueError rather than becoming a
    schema that means something else: dict fields (a closed object with no
    properties can only ever be `{}`) and `oneOf` (discriminated unions).
    """
    if not _SCHEMA_NAME.fullmatch(model.__name__):
        raise ValueError(f"{model.__name__!r} can't name a schema: use letters, digits, _ or -")
    schema = model.model_json_schema()
    return OutputSchema(name=model.__name__, json_schema=_strict(schema, schema.get("$defs", {})))


_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _strict(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_strict(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "oneOf" in node:
        raise ValueError("strict structured output has no oneOf (discriminated unions)")
    if "$ref" in node and len(node) > 1:
        target = defs[node["$ref"].removeprefix("#/$defs/")]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    strict: dict[str, Any] = {}
    for key, value in node.items():
        if key in ("properties", "$defs"):  # names to schemas: a field may be called "title"
            strict[key] = {name: _strict(schema, defs) for name, schema in value.items()}
        elif key not in ("title", "default"):
            strict[key] = _strict(value, defs)
    if strict.get("type") == "object":
        if "properties" not in strict or strict.get("additionalProperties", False) is not False:
            raise ValueError("strict structured output has no dict fields: use a model or a list")
        strict["additionalProperties"] = False
        strict["required"] = list(strict["properties"])
    return strict


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
