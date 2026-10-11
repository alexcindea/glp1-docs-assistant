"""Smoke test: the eval harness end to end, on the real answer pipeline, with fake models.

A gold question is answered by app.answering (real Postgres, fake embedder,
scripted model), scored by answer_metrics, saved as answers.py saves it, graded
by the judge (a fake judge) and turned into calibration cases. The unit tests
cover each piece; this joins them, so a mismatch between what the pipeline emits
and what the harness reads fails in CI rather than halfway through a paid run.

Needs Docker, like the API's database tests, whose fixtures it borrows.
"""

import json
from pathlib import Path

import pytest
from answer_metrics import score
from answers import ask, row
from app.llm import CompletionRequest, FakeLLMProvider, ModelRole
from calibration import build
from gold import Expected, GoldItem
from judge import ClaimVerdict, Judge, JudgeVerdict, _row, case_for, grade_without_judge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import (  # noqa: F401  (fixtures, used by name)
    _ignore_dotenv,
    anyio_backend,
    database_url,
    migrated_database_url,
    pg_container,
    sessions,
)
from tests.test_answering import DOSE, EMBEDDER, add_corpus, cites_the_dose

GOLD = GoldItem(
    id="s1",
    question="What is the maximum dose of axolotlin?",
    history=[],
    answerable=True,
    expected=[Expected(doc_id="fixture", page=4, quote="maximum recommended dosage of axolotlin")],
    reference_answer="2 mg once weekly.",
    tags=["fact"],
)


def respond(request: CompletionRequest) -> str:
    if request.role == ModelRole.JUDGE:  # backs the claim with whichever passage was cited
        every = list(range(1, 11))
        claim = ClaimVerdict(claim="the maximum is 2 mg", cites=every, supported_by=every)
        return JudgeVerdict(claims=[claim], reason="backed", correct=True).model_dump_json()
    return cites_the_dose(request)


@pytest.mark.anyio
async def test_a_gold_question_goes_through_answering_scoring_judging_and_calibration(
    sessions: async_sessionmaker[AsyncSession],  # noqa: F811  (the imported fixture)
    tmp_path: Path,
) -> None:
    await add_corpus(sessions)
    llm = FakeLLMProvider(responder=respond)

    async with sessions() as session:
        outcome = await ask(session, EMBEDDER, llm, GOLD, "follow_ups")
    result = score(GOLD, outcome)
    saved = json.loads(json.dumps(row(GOLD, result, outcome)))  # as answers.json holds it

    assert result.verdict == "cited_gold"
    (cited,) = saved["citations"]
    assert (cited["doc_id"], cited["pages"], cited["text"]) == ("fixture", "4", DOSE)

    assert grade_without_judge(GOLD, saved) is None  # an answer: the judge grades it
    grade = await Judge(llm, "fake (none)", cache=tmp_path).grade(case_for(GOLD, saved))
    assert grade.correct and grade.all_backed
    assert json.loads(json.dumps(_row(grade)))["details"][0]["cites"] == [cited["n"]]

    cases = build([saved], [GOLD])
    assert sorted(c.kind for c in cases) == ["real", "wrong_number"]  # one copy per question
    assert len({c.split for c in cases}) == 1  # a copy shares its original's half
