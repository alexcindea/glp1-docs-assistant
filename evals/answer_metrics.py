"""Scoring grounded answers against the gold set, without judging the wording.

A gold question passes when the system did the right thing with evidence:

- answerable: it answered, and its citations include the gold passage (every
  gold passage for a `match: all` comparison, any one for `match: any`);
- not answerable: it refused (any reason: not found, out of scope).

"Cited the gold passage" uses the same matching as retrieval recall
(metrics.finds): same document, quote contained in the cited chunk.

Whether the answer's wording is right and fully supported is the judge's work
(phase 7). What this adds over recall is where a failure happened: an
answerable question that fails is split by whether retrieval had found the
gold passage at all, so "retrieval missed it" and "the model failed" differ.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from gold import GoldItem
from metrics import Hit, finds

Verdict = Literal[
    "cited_gold",  # answerable, answered, cites the gold passage(s): pass
    "cited_other",  # answerable, answered, but not citing the gold passage(s)
    "false_refusal",  # answerable, refused
    "refused",  # not answerable, refused: pass
    "answered",  # not answerable, answered anyway
    "error",  # the pipeline ended with an error event
]
PASSING: frozenset[Verdict] = frozenset({"cited_gold", "refused"})


@dataclass(frozen=True)
class Outcome:
    """What the pipeline did with one gold question."""

    id: str
    sources: tuple[Hit, ...]  # the passages the model saw, in order
    cited: tuple[Hit, ...]  # the passages it cited
    refusal: str | None  # reason, when refused
    error: str | None  # code, when it ended with an error
    cost_usd: float | None = None
    first_token_ms: float | None = None
    total_ms: float | None = None
    search_query: str = ""
    answer: str = ""  # as shown, with [n] markers; "" when refused
    citations: tuple[str, ...] = ()  # "doc p.N", for reading the results


@dataclass(frozen=True)
class Score:
    id: str
    tags: tuple[str, ...]
    verdict: Verdict
    retrieved: bool | None  # gold passage(s) among the sources; None if not answerable
    citation_recall: float | None  # share of gold passages cited; None if not answerable

    @property
    def passed(self) -> bool:
        return self.verdict in PASSING


def covered(item: GoldItem, hits: Sequence[Hit]) -> float:
    """Share of the item's gold passages found in `hits`; 0 or 1 for `match: any`."""
    found = [any(finds(expected, hit) for hit in hits) for expected in item.expected]
    if item.match == "any":
        return float(any(found))
    return sum(found) / len(found)


def score(item: GoldItem, outcome: Outcome) -> Score:
    if outcome.id != item.id:
        raise ValueError(f"outcome {outcome.id} scored against gold item {item.id}")
    tags = tuple(item.tags)
    if not item.answerable:
        verdict: Verdict = (
            "error" if outcome.error else "refused" if outcome.refusal else "answered"
        )
        return Score(item.id, tags, verdict, retrieved=None, citation_recall=None)
    retrieved = covered(item, outcome.sources) == 1.0
    citation_recall = covered(item, outcome.cited)
    if outcome.error:
        verdict = "error"
    elif outcome.refusal:
        verdict = "false_refusal"
    else:
        verdict = "cited_gold" if citation_recall == 1.0 else "cited_other"
    return Score(item.id, tags, verdict, retrieved, citation_recall)


def summarise(scores: Sequence[Score], outcomes: Sequence[Outcome]) -> dict[str, object]:
    """Rates over the whole set and its two halves, plus cost and latency."""
    answerable = [s for s in scores if s.retrieved is not None]
    unanswerable = [s for s in scores if s.retrieved is None]
    costs = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    first = [o.first_token_ms for o in outcomes if o.first_token_ms is not None]
    totals = [o.total_ms for o in outcomes if o.total_ms is not None]
    verdicts = {v: sum(s.verdict == v for s in scores) for v in get_args(Verdict)}
    return {
        "questions": len(scores),
        "pass_rate": _rate([s.passed for s in scores]),
        "answerable": len(answerable),
        "answered_with_gold_citation": _rate([s.passed for s in answerable]),
        "false_refusal_rate": _rate([s.verdict == "false_refusal" for s in answerable]),
        "citation_recall": round(statistics.fmean(s.citation_recall or 0 for s in answerable), 3)
        if answerable
        else None,
        "retrieved": _rate([bool(s.retrieved) for s in answerable]),
        "failed_although_retrieved": sum(not s.passed and bool(s.retrieved) for s in answerable),
        "unanswerable": len(unanswerable),
        "correct_refusal_rate": _rate([s.passed for s in unanswerable]),
        "verdicts": verdicts,
        "cost_usd_mean": round(statistics.fmean(costs), 5) if costs else None,
        "cost_usd_total": round(sum(costs), 4),
        "first_token_ms_median": _percentile(first, 0.5),
        "first_token_ms_p95": _percentile(first, 0.95),
        "total_ms_median": _percentile(totals, 0.5),
        "total_ms_p95": _percentile(totals, 0.95),
    }


def _rate(flags: Sequence[bool]) -> float | None:
    return round(sum(flags) / len(flags), 3) if flags else None


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 1)
