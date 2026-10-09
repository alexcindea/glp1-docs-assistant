from answer_metrics import Outcome, score, summarise
from gold import Expected
from metrics import Hit
from test_metrics import DOSE, item

GOLD_HIT = Hit(
    doc_id="label", text="2.2 Dosage. The maximum recommended dosage is 2 mg once weekly."
)
OTHER = Hit(doc_id="label", text="Start at 0.25 mg once weekly.")
START = Expected(doc_id="label", page=3, quote="Start at 0.25 mg once weekly.")


def outcome(
    *,
    sources: tuple[Hit, ...] = (OTHER, GOLD_HIT),
    cited: tuple[Hit, ...] = (),
    refusal: str | None = None,
    error: str | None = None,
) -> Outcome:
    return Outcome(id="q", sources=sources, cited=cited, refusal=refusal, error=error)


def test_an_answer_citing_the_gold_passage_passes() -> None:
    result = score(item(DOSE), outcome(cited=(GOLD_HIT,)))

    assert result.verdict == "cited_gold"
    assert result.passed
    assert (result.retrieved, result.citation_recall) == (True, 1.0)


def test_an_answer_citing_something_else_fails() -> None:
    result = score(item(DOSE), outcome(cited=(OTHER,)))

    assert result.verdict == "cited_other"
    assert not result.passed


def test_a_refused_answerable_question_says_whether_retrieval_had_the_passage() -> None:
    had_it = score(item(DOSE), outcome(refusal="not_found"))
    missed = score(item(DOSE), outcome(sources=(OTHER,), refusal="not_found"))

    assert had_it.verdict == missed.verdict == "false_refusal"
    assert (had_it.retrieved, missed.retrieved) == (True, False)


def test_a_comparison_needs_every_gold_passage_cited() -> None:
    both = item(DOSE, START)

    half = score(both, outcome(cited=(GOLD_HIT,)))
    whole = score(both, outcome(cited=(GOLD_HIT, OTHER)))

    assert (half.verdict, half.citation_recall) == ("cited_other", 0.5)
    assert whole.verdict == "cited_gold"


def test_alternative_passages_need_only_one() -> None:
    either = item(DOSE, START, match="any")

    assert score(either, outcome(cited=(OTHER,))).verdict == "cited_gold"


def test_unanswerable_questions_pass_only_when_refused() -> None:
    unanswerable = item()

    assert score(unanswerable, outcome(refusal="out_of_scope")).verdict == "refused"
    assert score(unanswerable, outcome(cited=(OTHER,))).verdict == "answered"
    assert score(unanswerable, outcome(error="llm_unavailable")).verdict == "error"


def test_the_summary_splits_answerable_and_unanswerable() -> None:
    results = [
        score(item(DOSE), outcome(cited=(GOLD_HIT,))),
        score(item(DOSE), outcome(refusal="not_found")),
        score(item(), outcome(refusal="not_found")),
    ]
    outcomes = [
        Outcome("q", (), (), None, None, cost_usd=0.006, first_token_ms=2000, total_ms=3000),
        Outcome("q", (), (), "not_found", None, cost_usd=0.004, total_ms=2500),
        Outcome("q", (), (), "not_found", None, cost_usd=0.004, total_ms=2400),
    ]

    summary = summarise(results, outcomes)

    assert summary["pass_rate"] == 0.667
    assert summary["answered_with_gold_citation"] == 0.5
    assert summary["false_refusal_rate"] == 0.5
    assert summary["failed_although_retrieved"] == 1
    assert summary["correct_refusal_rate"] == 1.0
    assert summary["cost_usd_total"] == 0.014
    assert summary["first_token_ms_median"] == 2000
