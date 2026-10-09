# Check of `gold.draft.jsonl` against the PDFs (by Claude, 2026-10-09)

Read-only check: every quote was looked up on its stated **PDF page** (text-layer pages
automatically; scanned pages rendered as images and read by eye). Nothing was changed in the
drafts. `gold.jsonl` is yours to write; this file only tells you where to look.

## Result

| | Count | Items |
|---|---|---|
| Quote found on the stated page | 31 | everything not listed below, plus q007, q028 |
| Scanned page, quote confirmed by eye | 5 | q020, q021, q022, q023, q027 |
| Designed to have no answer, confirmed absent | 3 | q031 (price), q032 (retatrutide), q033 (SELECT only as "ongoing") |
| Designed to have no answer, nothing to check | 2 | q034 (medical advice), q036 (prompt injection) |
| **Needs your decision** | **3** | **q020, q007, q028** (below) |

## Needs your decision

**q020: the question is ambiguous.** It asks when the Victoza summary review was *signed*.
PDF page 2 shows the memo date, **January 25, 2010** (your reference answer). But the only
signature page in this 65-page PDF (the last page) is a different reviewer, Mary H. Parks,
signed **01/22/2010**. A correct system could cite either. Suggest rewording to "...and what
date is on the summary review?"

**q007 and q028: the same text is on several pages.** Your stated page (3) is right, but the
same sentences also appear elsewhere in the same label:
- q007 (Mounjaro dose increase): page 3, and also page 1 (the label's Highlights section).
- q028 (Mounjaro missed dose): page 3, and also page 25 (patient counselling) and page 27
  (patient information).

If the eval accepts only page 3, retrieval that returns page 1 or 27 counts as a miss even
though the passage says the same thing. Options: list the extra pages as additional accepted
locations, or decide that page 3 (the main prescribing section) is the one you want cited.
Labels repeat themselves like this a lot, so this will come up again.

## What was done about it

`evals/gold.jsonl` = the draft with **q020 reworded** as suggested; nothing else
changed. q007 and q028 keep page 3: if the retrieval metric matches on document + quote text
rather than page number alone, the repeated copies on other pages are accepted automatically.
That is a choice for the metric code in phase 4.

## Notes

- `q019`, `q024`, `q025`, `q026` and `q027` have several expected passages; all were found.
- Text extraction splits some words on these pages ("c urrent", "mi ssed"). That's the PDF, not
  the quotes, but it's a preview of what chunking and BM25 will have to cope with in phase 3.
- Checked with pypdf and pypdfium2 as one-off tools; neither was added to the project.
