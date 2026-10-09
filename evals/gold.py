"""The gold set: questions with the passages that answer them.

Read-only. `evals/gold.jsonl` is ground truth owned by the project author; nothing
here filters, rewrites or skips items.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

GOLD_PATH = Path(__file__).resolve().parent / "gold.jsonl"


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str  # the PDF's file name without ".pdf"
    page: int
    quote: str


class GoldItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    history: list[str]
    answerable: bool
    expected: list[Expected]
    # "all": every passage is needed (a comparison needs both sides). "any": each one
    # answers the question on its own (a label states the same fact in several places).
    match: Literal["all", "any"] = "all"
    reference_answer: str | None
    tags: list[str]

    @property
    def search_query(self) -> str:
        """What retrieval searches for.

        Follow-ups ("And for Mounjaro?") mean nothing alone. Until the LLM rewrites
        them into standalone questions (phase 5), the earlier turns are prepended.
        """
        return " ".join([*self.history, self.question])


def load_gold(path: Path = GOLD_PATH) -> list[GoldItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [GoldItem.model_validate_json(line) for line in lines if line.strip()]
