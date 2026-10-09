"""What a model call cost, from the tokens it used.

Prices are OpenAI's standard tier for prompts under 272K tokens, in USD per
million tokens, copied from https://developers.openai.com/api/docs/pricing on
2026-10-09. Our prompts are a few thousand tokens, so the long-context rates
never apply. Reasoning tokens are part of the output count and billed as output.

A model missing from the table has no known cost (None), rather than a wrong one.
"""

import re
from dataclasses import dataclass

from app.llm import Usage


@dataclass(frozen=True)
class Price:
    input: float
    cached_input: float
    cache_write: float
    output: float


PRICES: dict[str, Price] = {
    "gpt-6-astra": Price(input=10.00, cached_input=1.00, cache_write=12.50, output=50.00),
    "gpt-6.1-sol": Price(input=2.00, cached_input=0.10, cache_write=2.50, output=10.00),
    "gpt-6-luna": Price(input=0.10, cached_input=0.01, cache_write=0.125, output=0.50),
}
FREE = Price(input=0, cached_input=0, cache_write=0, output=0)
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def price_of(model: str) -> Price | None:
    """The price for `model`, or a dated snapshot of it ("gpt-6-luna-2026-07-14").

    Only a date may follow the name: "gpt-6-luna-mini" would be another model.
    """
    if model.startswith("fake-"):
        return FREE
    return PRICES.get(_SNAPSHOT.sub("", model))


def cost_usd(model: str, usage: Usage) -> float | None:
    price = price_of(model)
    if price is None:
        return None
    uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_input_tokens
    dollars_per_million = (
        uncached * price.input
        + usage.cached_input_tokens * price.cached_input
        + usage.cache_write_input_tokens * price.cache_write
        + usage.output_tokens * price.output
    )
    return dollars_per_million / 1_000_000
