"""How far the judge can be trusted: its verdicts against the calibration labels.

    uv run python evals/calibrate.py                   # tune half in full, holdout as numbers
    uv run python evals/calibrate.py --reveal-holdout  # once the prompt is frozen

Judges every case of evals/calibration/cases.jsonl (cached, like evals/judge.py)
and compares two verdicts with the labels: `correct`, and `backed` (every claim
backed by a passage it cites; an answer with no claims counts as backed, as the
labelling instructions say).

Per half and criterion it reports agreement, Cohen's kappa (agreement beyond
what two raters with the same yes/no rates would reach by chance) and the four
counts behind them, so a kappa from a handful of "no" labels is read for what
it is. It also reports how many broken copies the judge caught.

The discipline that makes the holdout number honest: the judge prompt may be
changed only while looking at the tune half. So by default only the tune half's
disagreements are listed; the holdout half shows numbers only, until
--reveal-holdout (after the prompt is frozen).

Writes evals/results/judge-calibration.json and .md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.config import get_settings
from app.providers import build_llm_provider
from calibration import CalibrationCase, Label, load_cases, load_labels
from judge import PROMPT_VERSION, Grade, Judge, judge_id

RESULTS = Path(__file__).resolve().parent / "results"
Criterion = Literal["correct", "backed"]
CRITERIA: tuple[Criterion, ...] = ("correct", "backed")


@dataclass(frozen=True)
class Pair:
    """One case: the label, and the judge's verdict on the same two questions."""

    case: CalibrationCase
    label: Label
    grade: Grade

    def human(self, criterion: Criterion) -> bool:
        return self.label.correct if criterion == "correct" else self.label.backed

    def judge(self, criterion: Criterion) -> bool:
        if criterion == "correct":
            return self.grade.correct
        return self.grade.all_backed is not False  # no claims: nothing unbacked


@dataclass(frozen=True)
class Agreement:
    n: int
    both_yes: int
    both_no: int
    judge_yes_label_no: int  # the judge let through what the label rejects
    judge_no_label_yes: int  # the judge rejected what the label accepts

    @property
    def rate(self) -> float | None:
        return round((self.both_yes + self.both_no) / self.n, 3) if self.n else None

    @property
    def kappa(self) -> float | None:
        """Cohen's kappa; None when chance agreement is total (one class only)."""
        if not self.n:
            return None
        observed = (self.both_yes + self.both_no) / self.n
        judge_yes = (self.both_yes + self.judge_yes_label_no) / self.n
        label_yes = (self.both_yes + self.judge_no_label_yes) / self.n
        chance = judge_yes * label_yes + (1 - judge_yes) * (1 - label_yes)
        if chance == 1:
            return None
        return round((observed - chance) / (1 - chance), 3)


def agreement(pairs: Sequence[Pair], criterion: Criterion) -> Agreement:
    def count(judge: bool, human: bool) -> int:
        return sum(p.judge(criterion) is judge and p.human(criterion) is human for p in pairs)

    return Agreement(
        n=len(pairs),
        both_yes=count(True, True),
        both_no=count(False, False),
        judge_yes_label_no=count(True, False),
        judge_no_label_yes=count(False, True),
    )


def caught(pairs: Sequence[Pair]) -> dict[str, int]:
    """Broken copies: how many the label rejects on some criterion, and how many of
    those the judge rejects on that same criterion; and, the other way round, how
    many copies the label accepts (the break did no harm) that the judge rejects."""
    broken = [p for p in pairs if p.case.kind != "real"]
    rejected = [p for p in broken if not (p.label.correct and p.label.backed)]
    accepted = [p for p in broken if p.label.correct and p.label.backed]
    hits = [p for p in rejected if any(not p.human(c) and not p.judge(c) for c in CRITERIA)]
    alarms = [p for p in accepted if any(not p.judge(c) for c in CRITERIA)]
    return {
        "broken": len(broken),
        "rejected_by_label": len(rejected),
        "caught": len(hits),
        "accepted_by_label": len(accepted),
        "false_alarms": len(alarms),
    }


async def judge_cases(cases: Sequence[CalibrationCase]) -> tuple[list[Grade], int]:
    settings = get_settings()
    if settings.llm_provider != "openai":
        raise SystemExit("Set LLM_PROVIDER=openai and OPENAI_API_KEY in .env: the judge is real.")
    judge = Judge(build_llm_provider(settings), judge_id(settings))
    grades = []
    for n, case in enumerate(cases, 1):
        grades.append(await judge.grade(case.case))
        print(f"{n}/{len(cases)} judged", flush=True)
    return grades, judge.calls


def report(pairs: Sequence[Pair], reveal_holdout: bool) -> dict[str, Any]:
    halves: dict[str, list[Pair]] = {
        "tune": [p for p in pairs if p.case.split == "tune"],
        "holdout": [p for p in pairs if p.case.split == "holdout"],
        "all": list(pairs),
    }
    summary = {
        half: {c: vars(agreement(members, c)) | _rates(agreement(members, c)) for c in CRITERIA}
        for half, members in halves.items()
    }
    disagreements = [
        {
            "case": n,
            "id": p.case.case.id,
            "split": p.case.split,
            "criteria": [c for c in CRITERIA if p.judge(c) != p.human(c)],
            "judge": {c: p.judge(c) for c in CRITERIA},
            "label": {c: p.human(c) for c in CRITERIA},
            "judge_reason": p.grade.reason,
            "label_note": p.label.note,
        }
        for n, p in enumerate(pairs, 1)
        if (reveal_holdout or p.case.split == "tune")
        and any(p.judge(c) != p.human(c) for c in CRITERIA)
    ]
    return {
        "agreement": summary,
        "broken_copies": {half: caught(members) for half, members in halves.items()},
        "disagreements": disagreements,
        "holdout_revealed": reveal_holdout,
    }


def _rates(a: Agreement) -> dict[str, float | None]:
    return {"rate": a.rate, "kappa": a.kappa}


def write(result: dict[str, Any], calls: int) -> None:
    settings = get_settings()
    document = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge": judge_id(settings),
        "prompt_version": PROMPT_VERSION,
        **result,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "judge-calibration.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    rows = []
    for half in ("holdout", "tune", "all"):
        for criterion in CRITERIA:
            a = result["agreement"][half][criterion]
            rows.append(
                f"| {half} | {criterion} | {a['n']} | {a['rate']} | {a['kappa']} | "
                f"{a['both_yes']} | {a['both_no']} | {a['judge_yes_label_no']} | "
                f"{a['judge_no_label_yes']} |"
            )
    broken = result["broken_copies"]
    lines = [
        "# Judge calibration",
        "",
        (
            f"Generated by `evals/calibrate.py`: judge {document['judge']}, prompt "
            f"{PROMPT_VERSION}, against the labels in `evals/calibration/labels.jsonl` "
            f"({result['agreement']['all']['correct']['n']} cases: real answers and deliberately broken copies). Kappa is "
            "Cohen's: "
            "agreement beyond chance. The counts say where the judge and the labels differ."
        ),
        "",
        (
            "| half | criterion | n | agreement | kappa | both yes | both no | "
            "judge yes, label no | judge no, label yes |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        *rows,
        "",
        "Broken copies the labels reject, caught by the judge on the same criterion: "
        + ", ".join(
            f"{half} {broken[half]['caught']} of {broken[half]['rejected_by_label']}"
            for half in ("holdout", "tune", "all")
        )
        + ". Broken copies the labels accept (the break did no harm) that the judge still "
        "rejects: "
        + ", ".join(
            f"{half} {broken[half]['false_alarms']} of {broken[half]['accepted_by_label']}"
            for half in ("holdout", "tune", "all")
        )
        + ".",
        "",
        "## Disagreements"
        + ("" if result["holdout_revealed"] else " (tune half only; holdout not revealed)"),
        "",
        "| case | half | on | judge | label note |",
        "|---|---|---|---|---|",
        *(
            f"| {d['case']} | {d['split']} | {', '.join(d['criteria'])} | "
            f"{_cell(d['judge_reason'])} | {_cell(d['label_note'])} |"
            for d in result["disagreements"]
        ),
        "",
        f"Judge calls made for this report: {calls} (the rest came from the cache).",
        "",
    ]
    (RESULTS / "judge-calibration.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reveal-holdout", action="store_true")
    args = parser.parse_args()
    cases, labels = load_cases(), load_labels()
    missing = [c.case.id for c in cases if c.case.id not in labels]
    if missing:
        raise SystemExit(f"{len(missing)} cases have no label yet: run evals/label.py")
    grades, calls = asyncio.run(judge_cases(cases))
    pairs = [Pair(c, labels[c.case.id], g) for c, g in zip(cases, grades, strict=True)]
    result = report(pairs, args.reveal_holdout)
    write(result, calls)
    for half in ("holdout", "tune"):
        for criterion in CRITERIA:
            a = result["agreement"][half][criterion]
            print(f"{half:8} {criterion:8} agreement {a['rate']}  kappa {a['kappa']}  (n={a['n']})")
    print(f"disagreements listed: {len(result['disagreements'])}; judge calls: {calls}")


if __name__ == "__main__":
    main()
