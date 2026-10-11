"""The calibration set: answers a person labels, to measure how far the judge can be trusted.

Real answers alone are not enough: most of them are right (that is what the
evidence eval says), so agreement on them can't show whether the judge catches
a wrong one. The set therefore adds a few deliberately broken copies of real
answers, each broken one way:

- wrong_number: the first number in the answer changed (2 mg -> 4 mg);
- swapped_citation: the answer's two most cited passages swap numbers, so the
  facts stay right but point at the wrong passage;
- dropped_fact: the answer's first sentence (it answers first) removed;
- added_claim: an invented sentence appended, citing a passage it cites.

The kind is only how the copy was made. The label is what counts: a broken copy
can still be right (the dropped sentence held no key fact), and a person decides.

Each case goes to one half, `tune` or `holdout`: the judge prompt may be
changed while looking at the tune half only, and the reported agreement is
measured on the holdout half. Halves are assigned per gold question, so a broken
copy is always in its original's half (tuning on one must not leak into the
other). Questions with a broken copy alternate first, so each half gets broken
cases of every kind; the rest alternate after them.

    uv run python evals/calibration.py   # builds evals/calibration/cases.jsonl, once

Builds from the follow-up rewrite run in evals/results/answers.json. It refuses
to overwrite a set that already has labels.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from gold import GoldItem, load_gold
from judge import Case, case_for
from pydantic import BaseModel, ConfigDict

EVALS = Path(__file__).resolve().parent
CALIBRATION = EVALS / "calibration"
CASES_PATH = CALIBRATION / "cases.jsonl"
LABELS_PATH = CALIBRATION / "labels.jsonl"
RUN = "follow_ups"
SEED = 7  # fixed, so the set is the same every time it is built
PER_KIND = 3

Kind = Literal["real", "wrong_number", "swapped_citation", "dropped_fact", "added_claim"]
Split = Literal["tune", "holdout"]


class CalibrationCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case: Case
    kind: Kind
    source_id: str  # the gold question it came from
    split: Split


class Label(BaseModel):
    """A person's verdict on one case: the ground truth the judge is compared with."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    correct: bool  # gives the reference answer's key facts, contradicts none
    backed: bool  # every claim backed by a passage it cites
    note: str = ""


# Numbers standing alone: not a citation marker, not part of a name ("GLP-1").
_NUMBER = re.compile(r"(?<![\[\w.-])(\d+(?:\.\d+)?)(?![\w\]]|\.\d)")
_MARKER = re.compile(r"\[(\d+)\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z*-])|\n+")
INVENTED = (
    "The FDA withdrew this approval in 2019",
    "Patients over 65 must start at twice the usual dose",
    "Unopened pens may be kept at room temperature for up to two years",
)


def wrong_number(answer: str) -> str | None:
    for match in _NUMBER.finditer(answer):
        value = match.group(1)
        if len(value) == 4 and 1900 <= float(value) <= 2100:
            continue  # a year
        changed = str(int(value) * 2) if value.isdigit() and value != "0" else _plus_one(value)
        return answer[: match.start()] + changed + answer[match.end() :]
    return None


def _plus_one(value: str) -> str:
    places = len(value.partition(".")[2])
    return f"{float(value) + 1:.{places}f}"


def swapped_citation(answer: str) -> str | None:
    counts: dict[str, int] = {}
    for n in _MARKER.findall(answer):
        counts[n] = counts.get(n, 0) + 1
    if len(counts) < 2:
        return None
    a, b = sorted(counts, key=lambda n: -counts[n])[:2]
    return _MARKER.sub(lambda m: f"[{b if m[1] == a else a if m[1] == b else m[1]}]", answer)


def dropped_fact(answer: str) -> str | None:
    """The answer without its first sentence; the rest is kept exactly as written."""
    text = answer.strip()
    if text.startswith(("|", "-", "*", "#")):
        return None  # opens with a table, list or heading, not a sentence
    end = _SENTENCE_END.search(text)
    rest = text[end.end() :] if end else ""
    if not _MARKER.search(rest):
        return None  # nothing cited would be left
    return rest


def added_claim(answer: str, index: int) -> str | None:
    cited = _MARKER.search(answer)
    if not cited:
        return None
    return f"{answer.rstrip()} {INVENTED[index % len(INVENTED)]} [{cited[1]}]."


def build(rows: Sequence[dict[str, Any]], gold: Sequence[GoldItem]) -> list[CalibrationCase]:
    """Every answered question of the run, plus PER_KIND broken copies of each kind."""
    by_id = {item.id: item for item in gold}
    answered = [r for r in rows if not r["refusal"] and not r["error"]]
    real = [case_for(by_id[r["id"]], r) for r in answered]
    rng = random.Random(SEED)
    pool = list(real)
    rng.shuffle(pool)
    breakers: list[tuple[Kind, Callable[[str, int], str | None]]] = [
        ("wrong_number", lambda a, _: wrong_number(a)),
        ("swapped_citation", lambda a, _: swapped_citation(a)),
        ("dropped_fact", lambda a, _: dropped_fact(a)),
        ("added_claim", added_claim),
    ]
    made: list[tuple[Kind, Case, str]] = [("real", c, c.id) for c in real]
    used: set[str] = set()
    for kind, breaker in breakers:
        count = 0
        for case in pool:
            if count == PER_KIND:
                break
            if case.id in used:
                continue
            broken = breaker(case.answer, count)
            if broken is None or broken == case.answer:
                continue
            used.add(case.id)
            count += 1
            copy = case.model_copy(update={"id": f"{case.id}~{kind}", "answer": broken})
            made.append((kind, copy, case.id))
    broken_first = [source for kind, _, source in made if kind != "real"]
    order = broken_first + [c.id for c in pool if c.id not in used]
    halves: dict[str, Split] = {
        source: "tune" if n % 2 == 0 else "holdout" for n, source in enumerate(order)
    }
    cases = [
        CalibrationCase(case=case, kind=kind, source_id=source, split=halves[source])
        for kind, case, source in made
    ]
    rng.shuffle(cases)  # so a broken copy rarely follows its original
    return cases


def load_cases(path: Path = CASES_PATH) -> list[CalibrationCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [CalibrationCase.model_validate_json(line) for line in lines if line.strip()]


def load_labels(path: Path = LABELS_PATH) -> dict[str, Label]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    labels = [Label.model_validate_json(line) for line in lines if line.strip()]
    return {label.id: label for label in labels}  # a later label replaces an earlier one


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild even if labels exist")
    args = parser.parse_args()
    if load_labels() and not args.force:
        raise SystemExit(f"{LABELS_PATH} has labels; rebuilding would orphan them (--force).")
    saved = json.loads((EVALS / "results" / "answers.json").read_text(encoding="utf-8"))
    cases = build(saved["by_rewrite_setting"][RUN]["items"], load_gold())
    CALIBRATION.mkdir(exist_ok=True)
    CASES_PATH.write_text(
        "".join(c.model_dump_json() + "\n" for c in cases), encoding="utf-8", newline="\n"
    )
    kinds = {k: sum(c.kind == k for c in cases) for k in dict.fromkeys(c.kind for c in cases)}
    halves = {s: sum(c.split == s for c in cases) for s in ("tune", "holdout")}
    print(f"{len(cases)} cases -> {CASES_PATH.relative_to(EVALS.parent)}: {kinds}, {halves}")


if __name__ == "__main__":
    main()
