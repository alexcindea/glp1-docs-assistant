"""Cost per call, from the price table and the tokens used."""

import pytest

from app.llm import Usage
from app.pricing import PRICES, cost_usd, price_of


def test_cost_bills_cached_and_written_tokens_at_their_own_rates() -> None:
    # gpt-6.1-sol: input 2.00, cached 0.10, cache write 2.50, output 10.00 per 1M
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cached_input_tokens=400_000,
        cache_write_input_tokens=100_000,
        reasoning_tokens=60_000,  # already inside output_tokens: not billed twice
    )

    cost = cost_usd("gpt-6.1-sol", usage)

    assert cost == pytest.approx(500_000 * 2e-6 + 400_000 * 0.1e-6 + 100_000 * 2.5e-6 + 1.0)


def test_dated_snapshots_take_their_model_price() -> None:
    assert price_of("gpt-6-luna-2026-07-14") == PRICES["gpt-6-luna"]
    assert price_of("gpt-6.1-sol") == PRICES["gpt-6.1-sol"]


def test_unknown_models_have_no_cost_rather_than_a_wrong_one() -> None:
    assert cost_usd("gpt-6-lunar", Usage(input_tokens=10, output_tokens=10)) is None
    assert price_of("gpt-6") is None
    assert price_of("gpt-6-luna-mini") is None  # another model, not a luna snapshot


def test_the_fake_provider_is_free() -> None:
    assert cost_usd("fake-answer", Usage(input_tokens=10, output_tokens=10)) == 0
