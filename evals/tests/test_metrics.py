import pytest
from gold import Expected, GoldItem, load_gold
from metrics import Hit, first_rank, quote_share, recall_at, score_item


def item(*expected: Expected, history: list[str] | None = None, match: str = "all") -> GoldItem:
    return GoldItem.model_validate(
        {
            "id": "q",
            "question": "What?",
            "history": history or [],
            "answerable": bool(expected),
            "expected": list(expected),
            "reference_answer": None,
            "tags": ["fact"],
            "match": match,
        }
    )


DOSE = Expected(doc_id="label", page=4, quote="The maximum recommended dosage is 2 mg once weekly.")


def test_exact_match_ignores_case_punctuation_and_line_breaks() -> None:
    text = "2.2 Dosage\nthe MAXIMUM recommended\ndosage is 2 mg, once-weekly. Other text."
    assert quote_share(DOSE.quote, text) == 1.0


def test_table_quote_matches_a_markdown_row() -> None:
    row = "| Adverse reaction | Placebo | 0.5 mg | 1 mg |\n| Nausea | 6.1 | 15.8 | 20.3 |"
    assert quote_share("Nausea 6.1 15.8 20.3", row) == 1.0


def test_numbers_do_not_match_inside_longer_numbers() -> None:
    assert quote_share("6.1 mg", "16.1 mg") < 0.8


def test_ocr_noise_still_matches() -> None:
    quote = "thyroid C-cell adenomas at 7-fold the clinical exposure"
    ocr = "thyroid C- cell ademonas (at 7-fold the clin ical exposure)"
    assert quote_share(quote, ocr) >= 0.8


def test_words_scattered_over_a_long_chunk_do_not_match() -> None:
    quote = "the dose is increased after at least 4 weeks"
    filler = " ".join(f"word{i}" for i in range(40))
    scattered = f"the {filler} dose {filler} is increased {filler} after at least 4 weeks"
    assert quote_share(quote, scattered) < 0.8


def test_a_quote_cut_by_a_chunk_boundary_counts_if_most_of_it_is_there() -> None:
    quote = "one two three four five six seven eight nine ten"
    assert quote_share(quote, "... one two three four five six seven eight nine") >= 0.8
    assert quote_share(quote, "... one two three four five") < 0.8


def test_first_rank_requires_the_expected_document() -> None:
    hits = [
        Hit(doc_id="other-label", text=DOSE.quote),
        Hit(doc_id="label", text="unrelated"),
        Hit(doc_id="label", text=f"Intro. {DOSE.quote}"),
    ]
    assert first_rank(DOSE, hits) == 3
    assert first_rank(DOSE, hits[:2]) is None


def test_recall_counts_each_expected_passage() -> None:
    other = Expected(doc_id="review", page=5, quote="about a 6% reduction in body weight")
    result = score_item(
        item(DOSE, other),
        [Hit("x", "noise")] * 6 + [Hit("label", DOSE.quote)],
    )
    assert result.ranks == (7, None)
    assert result.recall_at(5) == 0.0
    assert result.recall_at(10) == 0.5
    assert recall_at([result, score_item(item(DOSE), [Hit("label", DOSE.quote)])], 10) == 0.75


def test_alternative_passages_count_as_found_when_any_one_is() -> None:
    patient_leaflet = Expected(doc_id="label", page=19, quote="Throw away the pen after 30 days.")
    question = item(DOSE, patient_leaflet, match="any")

    result = score_item(question, [Hit("x", "noise")] * 3 + [Hit("label", patient_leaflet.quote)])

    assert result.ranks == (None, 4)
    assert result.recall_at(5) == 1.0
    assert result.recall_at(3) == 0.0


def test_unanswerable_items_are_not_scored_for_recall() -> None:
    with pytest.raises(ValueError, match="no expected passages"):
        score_item(item(), [])


def test_follow_ups_search_with_their_history() -> None:
    follow_up = item(DOSE, history=["If I miss an Ozempic dose, what do I do?"])
    assert follow_up.search_query == "If I miss an Ozempic dose, what do I do? What?"


def test_the_gold_file_loads() -> None:
    items = load_gold()
    assert len({i.id for i in items}) == len(items)
    assert all(bool(i.expected) == i.answerable for i in items)
