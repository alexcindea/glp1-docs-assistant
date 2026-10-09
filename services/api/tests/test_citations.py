"""Citation markers: parsed, normalised, checked against the passages; refusals recognised."""

import pytest

from app.citations import check_answer, classify_start


def test_markers_are_normalised_to_one_number_per_bracket() -> None:
    checked = check_answer("2 mg once weekly [1, 3]. Start low [Passage 2]; titrate [2-4].", 5)

    assert checked.text == "2 mg once weekly [1][3]. Start low [2]; titrate [2][3][4]."
    assert checked.cited == [1, 3, 2, 4]  # order of first use
    assert checked.refusal is None


def test_numbers_that_name_no_passage_are_dropped_and_reported() -> None:
    checked = check_answer("Dose is 2 mg [2][7]. Taken weekly [0].", 3)

    assert checked.text == "Dose is 2 mg [2]. Taken weekly."
    assert checked.cited == [2]
    assert checked.invalid == [7, 0]


def test_brackets_that_are_not_citations_are_left_alone() -> None:
    checked = check_answer("Trials ran [2010-2014] in [2019] with adults aged [18-75] [1].", 10)

    assert checked.text == "Trials ran [2010-2014] in [2019] with adults aged [18-75] [1]."
    assert checked.cited == [1]
    assert checked.invalid == []


def test_a_range_past_the_last_passage_cites_nothing() -> None:
    checked = check_answer("The dose is 2 mg [1-20].", 10)

    assert checked.text == "The dose is 2 mg [1-20]."
    assert checked.refusal == "no_citations"


def test_spaced_and_worded_markers_count() -> None:
    checked = check_answer("2 mg [ 2 ], weekly [Passages 3 and 5].", 5)

    assert checked.text == "2 mg [2], weekly [3][5]."
    assert checked.cited == [2, 3, 5]


def test_an_answer_without_a_valid_citation_is_refused() -> None:
    assert check_answer("The dose is 2 mg.", 3).refusal == "no_citations"
    assert check_answer("The dose is 2 mg [9].", 3).refusal == "no_citations"


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("NOT_FOUND", "not_found"),
        ("  NOT_FOUND.\n", "not_found"),
        ("OUT_OF_SCOPE", "out_of_scope"),
        ("not_found", "not_found"),
        ("**NOT_FOUND**", "not_found"),
        ("`OUT_OF_SCOPE`", "out_of_scope"),
    ],
)
def test_sentinels_are_refusals_with_no_text(reply: str, reason: str) -> None:
    checked = check_answer(reply, 3)

    assert checked.refusal == reason
    assert checked.text == ""


@pytest.mark.parametrize(
    ("start", "verdict"),
    [
        ("", "undecided"),
        ("  ", "undecided"),
        ("NOT_F", "undecided"),
        ("OUT", "undecided"),
        ("N", "undecided"),
        ("NOT_FOUND", "undecided"),  # could still be NOT_FOUNDATION
        ("NOT_FOUND.", "refusal"),
        ("OUT_OF_SCOPE\n", "refusal"),
        ("**NOT_FOUND*", "refusal"),
        ("`OUT", "undecided"),
        ("NOT_FOUNDATION", "answer"),
        ("The maximum", "answer"),
        ("NOTE: the label", "answer"),
        ("No, the documents", "answer"),
    ],
)
def test_the_start_of_a_stream_is_classified_as_soon_as_possible(start: str, verdict: str) -> None:
    assert classify_start(start) == verdict


def test_a_word_that_starts_like_a_sentinel_is_an_answer() -> None:
    checked = check_answer("NOT_FOUNDATION trial results were positive [1].", 3)

    assert checked.refusal is None
    assert checked.cited == [1]
