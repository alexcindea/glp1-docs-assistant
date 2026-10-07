# Decisions

One entry per non-obvious choice, newest first. Written in my own words; these
become the "Key technical decisions" section of the README.

Template:

```
## YYYY-MM-DD — Title
**Context:** the problem, one or two sentences.
**Options:** A (trade-off) · B (trade-off) · C (trade-off)
**Decision:** what I chose.
**Why:** the deciding reasons, with numbers where I measured something.
**Consequences:** what it costs, what I'll have to handle later, what would make me revisit it.
```

---

## 2026-10-07 — Demo documents: 18 FDA and EMA PDFs, listed in a pinned manifest
**Context:** The app needs a realistic set of documents that makes retrieval and citations
genuinely hard, without making setup slow for someone cloning the repo.
**Options:** ~27 documents (more coverage, about twice the ingest time and test-writing effort) ·
18-document core (every GLP-1 molecule and era, FDA and EMA views of the same drugs) ·
a handful of documents (fast, but too little overlap to stress retrieval)
**Decision:** The 18-document core: 12 FDA (labels, approval letter, summary and medical reviews)
and 6 EMA (assessment reports, one product information), 2005–2026. They are listed in
`scripts/corpus.json` with sha256 pins and downloaded by `scripts/fetch_corpus.py`
(standard library only). The PDFs themselves are never committed.
**Why:** Difficulty comes from near-duplicate content (the same warnings and trial language
across semaglutide and tirzepatide documents), not from volume. Measured: 1,460 pages, 66 MB,
~20 s to download; 282 pages (19%) have no text layer, including two fully scanned reviews
(Byetta 2005, Victoza 2010), so the OCR path is exercised per page, not per document.
FDA documents are public domain; EMA documents may be reused with the source acknowledged.
**Consequences:** The EMA product-information URL always serves the latest version, so its pin
will break when EMA updates it; the script fails loudly rather than silently using a different
file. If ingest turns out slow, add a small quickstart subset for reviewers. The README must
credit EMA as a source.
