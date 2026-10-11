"""Label the calibration set by hand, in the terminal.

    uv run python evals/label.py            # the cases not labelled yet
    uv run python evals/label.py --redo 7   # label case 7 again

For each case it shows the question, the reference answer, the answer and the
passages it cites, then asks the two questions in INSTRUCTIONS. They use the
judge's own definitions (evals/judge.py), so the two can be compared.

The labeller is blind: cases are shown by their place in the set ("case 7 of
41"), never by id, since a broken copy's id says how it was broken; and the
judge's verdict is never shown.

Each label is appended to evals/calibration/labels.jsonl as soon as it is
given, so you can stop (q) and come back. Skipped cases (s) come back next time.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import textwrap

from calibration import LABELS_PATH, CalibrationCase, Label, load_cases, load_labels
from judge import NO_ANSWER

WIDTH = min(100, shutil.get_terminal_size((100, 40)).columns - 2)

INSTRUCTIONS = """Two questions per answer (y/n):

- Correct? Does the answer give the reference answer's key facts, and
  contradict none of them? Wording, order and extra detail that is not wrong
  don't matter. When the documents don't answer the question, correct means
  the answer says so and offers nothing as the answer.
- Backed? Is every claim backed by a passage it cites? A claim is a statement
  of fact the answer presents as coming from the documents; framing ("The label
  says:") and saying the documents don't cover something are not claims. A
  claim's citation is the marker at the end of its sentence, or the next one in
  its paragraph. If the answer makes no claims at all, answer y.

Use the note for close calls (e.g. "a detail is missing, but not a key one")."""


def show(case: CalibrationCase, position: str) -> None:
    c = case.case
    rule = "=" * WIDTH
    print(f"\n{rule}\n{position}\n{rule}")  # never the id: it names how a copy was broken
    for earlier in c.history:
        _block("Earlier question", earlier)
    _block("Question", c.question)
    _block("Reference answer", c.reference_answer or NO_ANSWER)
    _block("Answer", c.answer)
    for passage in c.citations:
        where = f"{passage.doc_id} p.{passage.pages} | {passage.section}"
        _block(f"[{passage.n}] {where}", passage.text, indent="    ")


def _block(title: str, text: str, indent: str = "  ") -> None:
    print(f"\n{title}:")
    for paragraph in text.splitlines() or [""]:
        print(textwrap.fill(paragraph, WIDTH, initial_indent=indent, subsequent_indent=indent))


def ask(prompt: str) -> str:
    while True:
        reply = input(f"{prompt} [y/n, s skip, q quit] ").strip().lower()
        if reply in ("y", "n", "s", "q"):
            return reply
        print("  please answer y, n, s or q")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--redo", type=int, metavar="N", help="label case N again")
    args = parser.parse_args()
    cases = load_cases()
    labels = load_labels()
    numbered = list(enumerate(cases, 1))  # the set's own order, so a case keeps its number
    if args.redo:
        todo = [(n, c) for n, c in numbered if n == args.redo]
        if not todo:
            raise SystemExit(f"no case {args.redo}: the set has {len(cases)}")
    else:
        todo = [(n, c) for n, c in numbered if c.case.id not in labels]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print(INSTRUCTIONS)
    print(f"\n{len(labels)} of {len(cases)} labelled; {len(todo)} to go.")
    LABELS_PATH.parent.mkdir(exist_ok=True)
    for n, case in todo:
        show(case, f"Case {n} of {len(cases)}")
        correct = ask("\nCorrect?")
        if correct == "q":
            break
        if correct == "s":
            continue
        backed = ask("Backed?")
        if backed == "q":
            break
        if backed == "s":
            continue
        note = input("Note (optional, Enter to skip): ").strip()
        label = Label(id=case.case.id, correct=correct == "y", backed=backed == "y", note=note)
        with LABELS_PATH.open("a", encoding="utf-8", newline="\n") as file:
            file.write(label.model_dump_json() + "\n")
    done = len(load_labels())
    print(f"\n{done} of {len(cases)} labelled -> {LABELS_PATH}")


if __name__ == "__main__":
    main()
