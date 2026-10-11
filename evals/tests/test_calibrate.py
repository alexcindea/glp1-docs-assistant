from calibrate import Agreement, Pair, agreement, caught, report
from calibration import CalibrationCase, Kind, Label, Split
from judge import Case, Grade

CASE = Case(
    id="q1", question="?", history=[], reference_answer="2 mg.", answer="2 mg [1].", citations=[]
)


def pair(
    label: tuple[bool, bool],
    judge: tuple[bool, bool | None],
    *,
    split: Split = "tune",
    kind: Kind = "real",
    id: str = "q1",
) -> Pair:
    correct, backed = judge
    claims = 0 if backed is None else 1
    grade = Grade(
        id=id,
        correct=correct,
        judged=True,
        claims=claims,
        backed_by_own=1 if backed else 0,
        reason="judge says",
    )
    return Pair(
        case=CalibrationCase(
            case=CASE.model_copy(update={"id": id}), kind=kind, source_id="q1", split=split
        ),
        label=Label(id=id, correct=label[0], backed=label[1], note="label says"),
        grade=grade,
    )


def test_kappa_is_agreement_beyond_chance() -> None:
    # 10 cases, 8 agree; both raters say yes 6 times in 10: chance agreement 0.52.
    a = Agreement(n=10, both_yes=5, both_no=3, judge_yes_label_no=1, judge_no_label_yes=1)

    assert a.rate == 0.8
    assert a.kappa == round((0.8 - 0.52) / (1 - 0.52), 3)


def test_kappa_is_undefined_when_both_raters_only_ever_say_yes() -> None:
    a = Agreement(n=4, both_yes=4, both_no=0, judge_yes_label_no=0, judge_no_label_yes=0)

    assert a.rate == 1.0 and a.kappa is None


def test_counts_say_which_way_the_judge_differs() -> None:
    pairs = [
        pair((True, True), (True, True)),
        pair((False, True), (True, True)),  # judge too lenient on correct
        pair((True, True), (False, True)),  # judge too strict on correct
        pair((False, False), (False, False)),
    ]

    a = agreement(pairs, "correct")

    assert (a.both_yes, a.both_no, a.judge_yes_label_no, a.judge_no_label_yes) == (1, 1, 1, 1)


def test_an_answer_without_claims_counts_as_backed() -> None:
    assert pair((True, True), (True, None)).judge("backed") is True


def test_a_broken_copy_is_caught_only_on_a_criterion_the_label_rejects() -> None:
    pairs = [
        pair((False, True), (False, True), kind="wrong_number"),  # caught on correct
        pair((True, False), (False, True), kind="swapped_citation"),  # wrong criterion
        pair((True, True), (True, True), kind="dropped_fact"),  # label accepts it
        pair((False, False), (False, False)),  # real: not counted
    ]

    assert caught(pairs) == {"broken": 3, "rejected_by_label": 2, "caught": 1}


def test_holdout_disagreements_stay_hidden_until_revealed() -> None:
    pairs = [
        pair((True, True), (False, True), split="tune", id="t1"),
        pair((True, True), (False, True), split="holdout", id="h1"),
    ]

    hidden = report(pairs, reveal_holdout=False)
    shown = report(pairs, reveal_holdout=True)

    assert [d["id"] for d in hidden["disagreements"]] == ["t1"]
    assert [d["id"] for d in shown["disagreements"]] == ["t1", "h1"]
    assert hidden["agreement"]["holdout"]["correct"]["n"] == 1  # the numbers are always there
