from pathlib import Path
from typing import Any

from calibration import (
    INVENTED,
    Label,
    added_claim,
    build,
    dropped_fact,
    load_labels,
    swapped_citation,
    wrong_number,
)
from gold import Expected, GoldItem

ANSWER = (
    "The maximum dose is 2 mg once weekly [3]. Start at 0.25 mg [5][3].\n\nGLP-1 drugs vary [5]."
)


def test_wrong_number_changes_the_first_plain_number_only() -> None:
    assert wrong_number(ANSWER) == ANSWER.replace("2 mg", "4 mg", 1)
    assert wrong_number("Start at 0.25 mg [1].") == "Start at 1.25 mg [1]."


def test_wrong_number_leaves_markers_years_and_names_alone() -> None:
    assert wrong_number("Approved in 2005 for GLP-1 use [2].") is None
    assert wrong_number("In 2005, 44% had antibodies [2].") == "In 2005, 88% had antibodies [2]."


def test_swapped_citation_swaps_the_two_most_cited_passages() -> None:
    swapped = swapped_citation(ANSWER)

    assert swapped == (
        "The maximum dose is 2 mg once weekly [5]. Start at 0.25 mg [3][5].\n\nGLP-1 drugs vary [3]."
    )
    assert swapped_citation("Only one passage [2], twice [2].") is None


def test_dropped_fact_removes_the_first_sentence_and_keeps_the_rest_as_written() -> None:
    assert dropped_fact(ANSWER) == "Start at 0.25 mg [5][3].\n\nGLP-1 drugs vary [5]."
    assert dropped_fact("| a | b |\n|---|---|\n| 1 [2] | 2 |") is None  # a table
    assert dropped_fact("One sentence [2]. Then nothing cited.") is None


def test_added_claim_cites_a_passage_the_answer_already_cites() -> None:
    assert added_claim(ANSWER, 1) == f"{ANSWER} {INVENTED[1]} [3]."
    assert added_claim("Nothing cited.", 0) is None


def test_a_later_label_replaces_an_earlier_one(tmp_path: Path) -> None:
    path = tmp_path / "labels.jsonl"
    first = Label(id="q1", correct=True, backed=True)
    redo = Label(id="q1", correct=False, backed=True, note="misread")
    path.write_text(first.model_dump_json() + "\n" + redo.model_dump_json() + "\n")

    assert load_labels(path) == {"q1": redo}
    assert load_labels(tmp_path / "missing.jsonl") == {}


def _gold(n: int) -> GoldItem:
    return GoldItem(
        id=f"q{n:02}",
        question=f"Question {n}?",
        history=[],
        answerable=True,
        expected=[Expected(doc_id="label", page=1, quote="dose")],
        reference_answer="2 mg.",
        tags=["fact"],
    )


def _row(n: int, **overrides: Any) -> dict[str, Any]:
    citation = {"doc_id": "label", "pages": "1", "section": "Dose", "text": "The dose is 2 mg."}
    return {
        "id": f"q{n:02}",
        "answer": ANSWER,
        "citations": [{"n": 3, **citation}, {"n": 5, **citation}],
        "refusal": None,
        "error": None,
        **overrides,
    }


GOLD = [_gold(n) for n in range(16)]
ROWS = [_row(n) for n in range(15)] + [_row(15, answer="", refusal="not_found")]


def test_the_set_holds_every_answer_and_three_broken_copies_of_each_kind() -> None:
    cases = build(ROWS, GOLD)

    kinds = [c.kind for c in cases]
    assert kinds.count("real") == 15  # the refusal is left out
    for kind in ("wrong_number", "swapped_citation", "dropped_fact", "added_claim"):
        assert kinds.count(kind) == 3


def test_a_broken_copy_is_always_in_its_originals_half() -> None:
    cases = build(ROWS, GOLD)

    halves: dict[str, set[str]] = {}
    for case in cases:
        halves.setdefault(case.source_id, set()).add(case.split)
    assert all(len(h) == 1 for h in halves.values())
    broken = [c for c in cases if c.kind != "real"]
    assert len({c.source_id for c in broken}) == len(broken)  # one copy per question


def test_both_halves_get_broken_cases_of_every_kind() -> None:
    cases = build(ROWS, GOLD)

    for kind in ("wrong_number", "swapped_citation", "dropped_fact", "added_claim"):
        assert {c.split for c in cases if c.kind == kind} == {"tune", "holdout"}


def test_building_twice_gives_the_same_set() -> None:
    assert build(ROWS, GOLD) == build(ROWS, GOLD)
