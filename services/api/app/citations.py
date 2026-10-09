"""From the model's text to checked citations, or a refusal.

The model cites passage numbers in square brackets: [3], [2][5], [2, 5],
[Passages 2 and 5], [1-3]. `check_answer` normalises every marker to one number
per bracket ("[2][5]"), drops numbers that name no passage (a model can invent
them), and decides whether the text is an answer at all:

- a reply that starts with a sentinel (NOT_FOUND, OUT_OF_SCOPE), even wrapped
  in Markdown or quotes, is a refusal;
- a reply with no valid citation is a refusal too ("no_citations"): we never
  show an answer that cites nothing.

Brackets that are not citations stay as they are: years ("[2019]"), and ranges
that don't fit the passages ("aged [18-75]" with 10 passages). Only a range
inside 1..N counts, so a range can never quietly cite every passage.

Whether each sentence carries a citation is judged by the eval (phase 7), not
here: a citation at the end of a paragraph often covers the sentences before it.

While the answer streams, `classify_start` decides from its first characters
whether it is a sentinel, so a refusal is never shown as text.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from app.answer_prompt import NOT_FOUND, OUT_OF_SCOPE, SENTINELS

RefusalReason = Literal["not_found", "out_of_scope", "no_passages", "no_citations"]

_SENTINEL_REASONS: dict[str, RefusalReason] = {
    NOT_FOUND: "not_found",
    OUT_OF_SCOPE: "out_of_scope",
}
_DECORATION = " \t\r\n*_`\"'"  # what a model may wrap a sentinel in: **NOT_FOUND**
_SENTINEL = re.compile(
    rf"^[{re.escape(_DECORATION)}]*({NOT_FOUND}|{OUT_OF_SCOPE})(?!\w)", re.IGNORECASE
)
_DASH = r"\s*[-\N{EN DASH}]\s*"  # a range: "1-3", or with an en dash
_SEPARATOR = r"(?:\s*[,;]\s*|\s+and\s+)"
_NUMBER = rf"\d+(?:{_DASH}\d+)?"
# Optional whitespace (kept with the marker), then a bracket holding numbers and
# ranges, optionally after "passage"/"passages".
_MARKER = re.compile(
    rf"(\s*)\[\s*(?:passages?\s*)?({_NUMBER}(?:{_SEPARATOR}{_NUMBER})*)\s*\]", re.IGNORECASE
)
# A bracket holding a number this large is a year or a count, not a passage.
_NOT_A_PASSAGE = 1000


@dataclass(frozen=True)
class CheckedAnswer:
    text: str  # markers normalised, invalid ones removed; "" for a sentinel refusal
    cited: list[int] = field(default_factory=list)  # passage numbers, in order of first use
    invalid: list[int] = field(default_factory=list)  # cited numbers that name no passage
    refusal: RefusalReason | None = None


def check_answer(text: str, passage_count: int) -> CheckedAnswer:
    """Validate the model's full reply against passages numbered 1..passage_count."""
    if sentinel := _SENTINEL.match(text):
        return CheckedAnswer(text="", refusal=_SENTINEL_REASONS[sentinel.group(1).upper()])

    cited: list[int] = []
    invalid: list[int] = []

    def normalise(match: re.Match[str]) -> str:
        numbers = _numbers(match.group(2), passage_count)
        if numbers is None:  # a year, or a range that isn't ours: leave it
            return match.group(0)
        valid = [n for n in numbers if 1 <= n <= passage_count]
        invalid.extend(n for n in numbers if n not in valid)
        for n in valid:
            if n not in cited:
                cited.append(n)
        if not valid:
            return ""
        return match.group(1) + "".join(f"[{n}]" for n in dict.fromkeys(valid))

    checked = _MARKER.sub(normalise, text.strip())
    return CheckedAnswer(
        text=checked,
        cited=cited,
        invalid=invalid,
        refusal=None if cited else "no_citations",
    )


def classify_start(text: str) -> Literal["undecided", "refusal", "answer"]:
    """What the start of a streaming reply is, as soon as that can be known.

    "undecided" while the text so far could still become a sentinel ("NOT_F"),
    and right after one, until the next character shows it isn't the start of
    a longer word ("NOT_FOUNDATION").
    """
    start = text.lstrip(_DECORATION).upper()
    if not start:
        return "undecided"
    for sentinel in SENTINELS:
        if start.startswith(sentinel):
            after = start[len(sentinel) :]
            if not after:
                return "undecided"
            return "answer" if re.match(r"\w", after) else "refusal"
    if any(sentinel.startswith(start) for sentinel in SENTINELS):
        return "undecided"
    return "answer"


def _numbers(group: str, passage_count: int) -> list[int] | None:
    """The passage numbers a marker names, or None if it isn't a citation."""
    numbers: list[int] = []
    for part in re.split(_SEPARATOR, group, flags=re.IGNORECASE):
        bounds = [int(b) for b in re.split(_DASH, part)]
        if max(bounds) >= _NOT_A_PASSAGE:
            return None
        if len(bounds) == 2:
            low, high = bounds
            if not 1 <= low < high <= passage_count:
                return None
            numbers.extend(range(low, high + 1))
        else:
            numbers.append(bounds[0])
    return numbers
