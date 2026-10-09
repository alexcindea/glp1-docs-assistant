"""Retrieval recall against the gold set.

A gold passage is *found* by a retrieved chunk when the chunk comes from the
expected document and contains the expected quote. Text is compared as
lower-case words (letters and digits), so whitespace, punctuation, line breaks
and table pipes don't matter: "Nausea 6.1 15.8" matches "| Nausea | 6.1 | 15.8 |".

Exact containment is tried first. When OCR noise ("ademonas") or a chunk
boundary breaks it, the chunk still counts if some short stretch of it holds at
least 80% of the quote's words in order. The stretch is limited to about the
quote's length, so common words scattered across a long chunk can't add up to
a false match.

The page in the gold item is not required to match: the same sentence on
another page of the same document is still the right evidence. (Labels repeat
text between the highlights and the full prescribing information.)

recall@k for one question = share of its expected passages found in the top k,
or, for a question whose passages are alternatives (`match: any`), 1 if any of
them is; for the set = mean over answerable questions. Unanswerable questions have no
passages and belong to the refusal metrics (phase 5), not to recall.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

from gold import Expected, GoldItem

MIN_SHARE = 0.8
_WORD = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=50_000)
def words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(unicodedata.normalize("NFKC", text).lower()))


def quote_share(quote: str, text: str) -> float:
    """The largest share of the quote's words found, in order, in a stretch of
    `text` about as long as the quote. 1.0 means the quote is contained exactly."""
    needle, haystack = words(quote), words(text)
    if not needle:
        return 0.0
    if _contains(haystack, needle):
        return 1.0
    # Cheap rejection before the windowed alignment: too few of the quote's
    # distinct words occur anywhere in the text.
    present = set(haystack)
    if sum(word in present for word in needle) < MIN_SHARE * len(needle):
        return 0.0
    # OCR can split a word in two, so the window is a little longer than the quote.
    window = math.ceil(len(needle) * 1.25) + 2
    best = 0
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(needle)
    for start in range(max(1, len(haystack) - window + 1)):
        matcher.set_seq1(haystack[start : start + window])
        best = max(best, sum(block.size for block in matcher.get_matching_blocks()))
        if best == len(needle):
            break
    return best / len(needle)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    # Joined with a separator that can't occur in a word, so "6 1" can't match "16 1".
    return f"\x00{'\x00'.join(needle)}\x00" in f"\x00{'\x00'.join(haystack)}\x00"


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk, as the metric sees it."""

    doc_id: str
    text: str


def finds(expected: Expected, hit: Hit) -> bool:
    return hit.doc_id == expected.doc_id and quote_share(expected.quote, hit.text) >= MIN_SHARE


def first_rank(expected: Expected, hits: Sequence[Hit]) -> int | None:
    """1-based rank of the first hit that finds the passage, or None."""
    return next((rank for rank, hit in enumerate(hits, 1) if finds(expected, hit)), None)


@dataclass(frozen=True)
class ItemResult:
    id: str
    tags: tuple[str, ...]
    ranks: tuple[int | None, ...]  # one per expected passage
    match: str = "all"  # "any": the passages are alternatives

    def recall_at(self, k: int) -> float:
        found = [rank is not None and rank <= k for rank in self.ranks]
        if self.match == "any":
            return float(any(found))
        return sum(found) / len(found)


def score_item(item: GoldItem, hits: Sequence[Hit]) -> ItemResult:
    if not item.answerable or not item.expected:
        raise ValueError(f"{item.id} has no expected passages; recall doesn't apply")
    return ItemResult(
        id=item.id,
        tags=tuple(item.tags),
        ranks=tuple(first_rank(expected, hits) for expected in item.expected),
        match=item.match,
    )


def recall_at(results: Sequence[ItemResult], k: int) -> float:
    return statistics.fmean(result.recall_at(k) for result in results) if results else 0.0
